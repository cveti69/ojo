"""In-memory session state. All mutations happen on the event loop."""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from . import config

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


@dataclass
class Session:
    id: str
    goal: str
    user_lang: str
    target_lang: str
    text_entry: str
    app_lang: Optional[str] = None
    history: List[dict] = field(default_factory=list)
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

    def touch(self) -> None:
        self.last_activity = time.monotonic()

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
        self.revision += 1
        self.goal = goal
        self.history.clear()
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

    def finish(self, text_ru: str = "Сессия завершена") -> None:
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


class SessionStore:
    def __init__(self, ttl_seconds: Optional[float] = None):
        self.ttl_seconds = config.SESSION_TTL_SECONDS if ttl_seconds is None else ttl_seconds
        self.sessions: Dict[str, Session] = {}

    def create(self, goal: str, user_lang: str, target_lang: str, text_entry: str) -> Session:
        session = Session(
            id="s_" + uuid.uuid4().hex[:16], goal=goal, user_lang=user_lang,
            target_lang=target_lang, text_entry=text_entry,
            app_lang=None if target_lang == "auto" else target_lang,
        )
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
