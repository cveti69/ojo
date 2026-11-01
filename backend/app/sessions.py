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
    small: bool = False  # The phone saw a small change that has settled, too small for the grey picture here.


@dataclass
class Session:
    id: str
    user_lang: str
    target_lang: str
    text_entry: str
    effort: Optional[str] = None
    character: str = ""
    eyes: bool = False
    chat: List[dict] = field(default_factory=list)  # The call as the model sees it.
    turn: int = 0
    heard: str = ""  # Their words, while the phone sends the screen as it is now.
    open_words: str = ""  # Their last words until the first sound of the answer: more words now are the same line.
    open_mark: int = 0
    seen_gray: Optional[bytes] = None  # The screen of the last turn: a new screen is measured against it.
    restarts: int = 0
    held: bool = False
    answer: Optional[asyncio.Task] = None
    ears: Any = None
    app_lang: Optional[str] = None  # What the model says the screen is written in.
    texts: List[str] = field(default_factory=list)
    step_id: Optional[str] = None
    step_sequence: int = 0
    broadcasting: bool = False
    ended: bool = False
    shown: bool = False  # The window outlines something on the screen they are on.
    last_activity: float = field(default_factory=time.monotonic)
    last_frame: Optional[Frame] = None
    last_answer: Optional[dict] = None
    last_hint: Optional[dict] = None
    last_say: Optional[dict] = None
    last_status: dict = field(default_factory=lambda: {
        "type": "status", "state": "waiting_frames", "text_ru": "Включи демонстрацию экрана"
    })
    last_done: Optional[dict] = None
    grounding: Optional[dict] = None
    timings: Dict[str, Any] = field(default_factory=dict)
    frame_text: Dict[int, List[dict]] = field(default_factory=dict)
    subscribers: Set[asyncio.Queue] = field(default_factory=set)
    call_log: Optional[CallLog] = None

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def note(self, kind: str, **fields: Any) -> None:
        if self.call_log:
            self.call_log.write(kind, fields)

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
        elif event["type"] == "say" and event["text_ru"]:
            self.last_say = event
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
        if self.last_say and self.last_say["turn"] == self.turn and self.open_words and not self.ended:
            queue.put_nowait({"type": "heard", "turn": self.turn})  # No sound of the answer yet: it went to a dead socket.
            queue.put_nowait(self.last_say)
        if self.last_hint and not self.ended:
            queue.put_nowait(self.last_hint)
        if self.last_done:
            queue.put_nowait(self.last_done)
        if self.ended:
            queue.put_nowait(None)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)

    def finish(self, text_ru: str = "Сессия завершена") -> None:
        self.note("end", turns=self.turn, steps=self.step_sequence)
        self.ended = True
        self.broadcasting = False
        self.heard = ""
        self.texts.clear()
        if not self.last_done:
            self.publish({"type": "done", "text_ru": text_ru})
        for queue in tuple(self.subscribers):
            self._put(queue, None)
        if self.answer and not self.answer.done():
            self.answer.cancel()
        if self.ears:
            self.ears.close()


class SessionStore:
    def __init__(self, ttl_seconds: Optional[float] = None):
        self.ttl_seconds = config.SESSION_TTL_SECONDS if ttl_seconds is None else ttl_seconds
        self.sessions: Dict[str, Session] = {}

    def create(self, user_lang: str, target_lang: str, text_entry: str, effort: Optional[str] = None,
               character: str = "", eyes: bool = False) -> Session:
        session = Session(
            id="s_" + uuid.uuid4().hex[:16], user_lang=user_lang, target_lang=target_lang, text_entry=text_entry,
            effort=effort, character=character, eyes=eyes,
        )
        session.call_log = CallLog.start(session.id)
        session.note("call", user_lang=user_lang, target_lang=target_lang, text_entry=text_entry, eyes=eyes,
                     model=config.META_TALK_MODEL, spare=config.META_SPARE_MODEL, effort=effort or config.META_TALK_REASONING)
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
            if session.answer:
                tasks.append(session.answer)
        self.sessions.clear()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
