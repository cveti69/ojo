"""The two layers of the conversation: a quick line from the words alone, then the answer from the screen."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import List, Optional, Sequence, Set, Tuple

from starlette.concurrency import run_in_threadpool

from . import talk
from .engine import HISTORY_STEPS, Engine
from .sessions import Session

log = logging.getLogger(__name__)

FAILED = "Не получилось ответить, повтори, пожалуйста."


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _drop(seeing: Optional[asyncio.Future]) -> None:
    """The model call itself cannot be stopped: it ends unused."""
    if seeing:
        seeing.add_done_callback(lambda task: task.cancelled() or task.exception())
        seeing.cancel()


class Talker:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.tasks: Set[asyncio.Task] = set()

    def heard(self, session: Session, said: str) -> None:
        """A turn from the stream. Answers run side by side, so a new turn never waits for an old one."""
        task = asyncio.create_task(self.answer(session, *self.take(session, said)))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def take(self, session: Session, said: str) -> Tuple[int, str]:
        """If they went on before the agent spoke, both turns are one line."""
        if session.open_words:
            said = session.open_words + " " + said
            del session.dialog[session.open_mark:]
            session.looking = 0
        session.open_words = said
        session.open_mark = len(session.dialog)
        session.turn += 1
        session.heard = ""
        session.publish({"type": "heard", "turn": session.turn})
        return session.turn, said

    def played(self, session: Session, turn: int) -> None:
        if turn == session.turn:
            session.open_words = ""

    async def answer(self, session: Session, turn: int, said: str) -> None:
        started = time.perf_counter()
        frame = session.last_frame if session.mode == "lead" else None
        # Leading, the frame is on the server already, so the answer from it gets a head start.
        seeing = asyncio.ensure_future(self._see(session, said, [frame.jpeg])) if frame else None
        try:
            quick = await run_in_threadpool(talk.check, self._prompt(session, said, 0, quick=True))
        except Exception as error:
            log.warning("Quick line: %s", type(error).__name__)
            _drop(seeing)
            if turn == session.turn:
                self._say(session, turn, said, {"reply": FAILED, "new_goal": None, "mode": None}, "quick", {})
            return
        timings = {"quick_ms": _ms(started)}
        if turn != session.turn or session.ended:
            _drop(seeing)
        elif quick["needs_screen"]:
            await self._look_after(session, turn, said, quick, seeing, timings, started)
        else:
            _drop(seeing)
            if quick["reply"]:
                self._say(session, turn, said, quick, "quick", timings)
            else:
                session.open_words = ""

    async def _look_after(self, session: Session, turn: int, said: str, quick: dict, seeing: Optional[asyncio.Future],
                          timings: dict, started: float) -> None:
        if session.mode == "beside":
            if quick["reply"]:
                self._say(session, turn, said, quick, "quick", timings)
            session.heard = said
            session.publish({"type": "look", "turn": turn})
            return
        seeing = seeing or asyncio.ensure_future(self._see(session, said, []))
        session.looking = turn
        if quick["reply"] and not seeing.done():
            self._say(session, turn, said, quick, "quick", timings)
        await self._from_screen(session, turn, said, seeing, timings, started)

    async def look(self, session: Session, pictures: Sequence[bytes]) -> None:
        """The screens the phone kept. They live only for this reply."""
        said, session.heard = session.heard, ""
        session.looking = session.turn
        await self._from_screen(session, session.turn, said, asyncio.ensure_future(self._see(session, said, pictures)),
                                {}, time.perf_counter())

    async def _from_screen(self, session: Session, turn: int, said: str, seeing: asyncio.Future, timings: dict,
                           started: float) -> None:
        """A turn that only chats leaves this answer standing. Only a newer question to the screen replaces it."""
        try:
            answer = await seeing
        except Exception as error:
            log.warning("Answer from the screen: %s", type(error).__name__)
            answer = {"reply": FAILED, "new_goal": None, "mode": None}
        timings["screen_ms"] = _ms(started)
        if session.looking == turn and not session.ended and answer["reply"]:
            self._say(session, turn, said, answer, "screen", timings)

    async def _see(self, session: Session, said: str, pictures: Sequence[bytes]) -> dict:
        return await run_in_threadpool(talk.ask, pictures, self._prompt(session, said, len(pictures)), session.effort)

    def _prompt(self, session: Session, said: str, screens: int, quick: bool = False) -> str:
        last_hint = session.last_hint or {}
        return talk.build_prompt(
            session.goal, [item["summary"] for item in session.history[-HISTORY_STEPS:]],
            last_hint.get("instruction_ru", ""), session.dialog[:session.open_mark], said, screens,
            user_lang=session.user_lang, target_lang=session.app_lang or session.target_lang,
            character=session.character, done=session.completed, quick=quick, mode=session.mode,
        )

    def _say(self, session: Session, turn: int, said: str, answer: dict, layer: str, timings: dict) -> None:
        if turn > session.written_turn:
            session.written_turn = turn
            session.dialog.append({"who": "person", "text": said})
        session.dialog.append({"who": "agent", "text": answer["reply"]})
        if answer["mode"]:
            session.set_mode(answer["mode"])
        if answer["new_goal"]:
            session.set_goal(answer["new_goal"])
            if session.broadcasting and session.last_frame:  # A still screen sends no frames.
                self.engine.submit(session, session.last_frame)
        session.publish({"type": "say", "turn": turn, "layer": layer, "text_ru": answer["reply"], "goal": answer["new_goal"],
                         "mode": answer["mode"], "timings": timings})
