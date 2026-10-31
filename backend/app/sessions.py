"""In-memory session state. All mutations happen on the event loop."""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from . import config
from .calllog import CallLog

KEPT_FRAME_TEXTS = 8


@dataclass(frozen=True)
class Frame:
    jpeg: bytes
    width: int
    height: int
    source_width: int
    source_height: int
    timestamp_ms: int
    gray: bytes
    digest: str
    received_at: float
    receive_ms: int
    window: Optional[List[float]] = None


@dataclass
class Session:
    id: str
    goal: str
    user_lang: str
    target_lang: str
    text_entry: str
    effort: Optional[str] = None
    character: str = ""
    mode: str = "lead"
    eyes: bool = False
    heard: str = ""
    turn: int = 0
    open_words: str = ""
    open_mark: int = 0
    looking: int = 0
    written_turn: int = 0
    ears: Any = None
    app_lang: Optional[str] = None
    history: List[dict] = field(default_factory=list)
    dialog: List[dict] = field(default_factory=list)
    last_frame_hash: Optional[str] = None
    last_analyzed_gray: Optional[bytes] = None
    last_step: Optional[dict] = None
    texts: List[str] = field(default_factory=list)
    insert_hint_ru: str = ""
    step_id: Optional[str] = None
    step_sequence: int = 0
    broadcasting: bool = False
    ended: bool = False
    completed: bool = False
    needs_user_confirm: bool = False
    busy: bool = False
    revision: int = 0
    analyzing_gray: Optional[bytes] = None
    stale: asyncio.Event = field(default_factory=asyncio.Event)
    restarts: int = 0
    last_activity: float = field(default_factory=time.monotonic)
    last_frame: Optional[Frame] = None
    pending_frame: Optional[Frame] = None
    last_answer: Optional[dict] = None
    last_hint: Optional[dict] = None
    last_status: dict = field(default_factory=lambda: {
        "type": "status", "state": "waiting_frames", "text_ru": "Включи демонстрацию экрана"
    })
    last_done: Optional[dict] = None
    grounding: Optional[dict] = None
    timings: Dict[str, Any] = field(default_factory=dict)
    frame_text: Dict[int, List[dict]] = field(default_factory=dict)
    task: Optional[asyncio.Task] = None
    subscribers: Set[asyncio.Queue] = field(default_factory=set)
    call_log: Optional[CallLog] = None

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def note(self, kind: str, **fields: Any) -> None:
        if self.call_log:
            self.call_log.write(kind, fields)

    def talk_so_far(self) -> List[dict]:
        """Everything said on the call, with the line still being answered."""
        if self.open_words and self.written_turn < self.turn:
            return self.dialog + [{"who": "person", "text": self.open_words}]
        return self.dialog

    def keep_picture(self, frame: Frame, private: bool) -> Optional[str]:
        """A payment or password screen is never written down."""
        return self.call_log.keep(frame.jpeg, frame.timestamp_ms) if self.call_log and not private else None

    def remember_text(self, timestamp_ms: int, lines: List[dict]) -> None:
        self.frame_text[timestamp_ms] = lines
        while len(self.frame_text) > KEPT_FRAME_TEXTS:
            del self.frame_text[next(iter(self.frame_text))]

    def publish(self, event: dict) -> None:
        if event["type"] == "status":
            self.last_status = event
        elif event["type"] == "hint":
            self.last_hint = event
        elif event["type"] == "done":
            self.last_done = event
        for queue in tuple(self.subscribers):
            self._put(queue, event)

    @staticmethod
    def _put(queue: asyncio.Queue, event: Optional[dict]) -> None:
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(event)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=16)
        self.subscribers.add(queue)
        queue.put_nowait(self.last_status)
        if self.last_hint and not self.completed and not self.ended:
            queue.put_nowait(self.last_hint)
        if self.last_done:
            queue.put_nowait(self.last_done)
        if self.ended:
            queue.put_nowait(None)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)

    def set_goal(self, goal: str) -> None:
        """A refined goal keeps the steps done so far: the person is still on the same road."""
        self.note("goal", goal=goal)
        self.revision += 1
        self.goal = goal
        self.texts.clear()
        self.insert_hint_ru = ""
        self.step_id = None
        self.last_frame_hash = None
        self.last_analyzed_gray = None
        self.last_step = None
        self.pending_frame = None
        self.last_answer = None
        self.last_hint = None
        self.last_done = None
        self.grounding = None
        self.timings = {}
        self.completed = False
        self.needs_user_confirm = False
        self.publish({
            "type": "status", "state": "broadcasting" if self.broadcasting else "waiting_frames",
            "text_ru": "Демонстрация экрана включена" if self.broadcasting else "Включи демонстрацию экрана",
        })
        self.touch()

    def set_mode(self, mode: str) -> None:
        """Beside drops what the server holds: the screen stays on the phone until the person speaks to the agent."""
        if mode == self.mode:
            return
        self.note("mode", mode=mode)
        self.mode = mode
        self.revision += 1
        self.pending_frame = None
        self.last_frame = None
        self.last_frame_hash = None
        self.last_analyzed_gray = None
        self.last_step = None
        self.frame_text.clear()
        self.completed = False
        self.touch()

    def finish(self, text_ru: str = "Сессия завершена") -> None:
        self.note("end", goal=self.goal, turns=self.turn, steps=self.step_sequence)
        self.ended = True
        self.revision += 1
        self.broadcasting = False
        self.pending_frame = None
        self.texts.clear()
        self.insert_hint_ru = ""
        if not self.last_done:
            self.publish({"type": "done", "text_ru": text_ru})
        for queue in tuple(self.subscribers):
            self._put(queue, None)
        if self.task and not self.task.done():
            self.task.cancel()
        if self.ears:
            self.ears.close()


class SessionStore:
    def __init__(self, ttl_seconds: Optional[float] = None):
        self.ttl_seconds = config.SESSION_TTL_SECONDS if ttl_seconds is None else ttl_seconds
        self.sessions: Dict[str, Session] = {}

    def create(self, goal: str, user_lang: str, target_lang: str, text_entry: str, effort: Optional[str] = None,
               character: str = "", mode: str = "lead", eyes: bool = False) -> Session:
        session = Session(
            id="s_" + uuid.uuid4().hex[:16], goal=goal, user_lang=user_lang,
            target_lang=target_lang, text_entry=text_entry, effort=effort, character=character, mode=mode, eyes=eyes,
            app_lang=None if target_lang == "auto" else target_lang,
        )
        session.call_log = CallLog.start(session.id)
        session.note("call", goal=goal, mode=mode, user_lang=user_lang, target_lang=target_lang, text_entry=text_entry,
                     effort=effort, eyes=eyes, pointer=config.META_VISION_MODEL, pointer_effort=config.META_REASONING,
                     head=config.META_TALK_MODEL, head_effort=config.META_TALK_REASONING, quick=config.META_QUICK_MODEL)
        self.sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Optional[Session]:
        session = self.sessions.get(session_id)
        if session and time.monotonic() - session.last_activity >= self.ttl_seconds:
            self._remove(session_id)
            return None
        if session:
            session.touch()
        return session

    def _remove(self, session_id: str) -> None:
        session = self.sessions.pop(session_id)
        session.finish("Сессия истекла")

    def cleanup(self) -> None:
        now = time.monotonic()
        for session_id, session in list(self.sessions.items()):
            if now - session.last_activity >= self.ttl_seconds:
                self._remove(session_id)

    async def reap(self) -> None:
        while True:
            await asyncio.sleep(max(0.01, config.SESSION_CLEANUP_SECONDS))
            self.cleanup()

    async def close(self) -> None:
        tasks = []
        for session in list(self.sessions.values()):
            session.finish()
            if session.task:
                tasks.append(session.task)
        self.sessions.clear()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
