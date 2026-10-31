"""The two layers of the conversation: a quick line from the words alone, then the head, which sees the screens."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional, Sequence, Set, Tuple

from starlette.concurrency import run_in_threadpool

from . import config, talk, window
from .engine import HISTORY_STEPS, Engine
from .sessions import Frame, Session

log = logging.getLogger(__name__)

FAILED_LINE = {"reply": "Не получилось ответить, повтори, пожалуйста.", "new_goal": None, "mode": None, "show": None, "private": True}


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


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
        session.note("heard", turn=session.turn, said=said)
        session.publish({"type": "heard", "turn": session.turn})
        return session.turn, said

    def played(self, session: Session, turn: int) -> None:
        if turn == session.turn:
            session.open_words = ""

    async def answer(self, session: Session, turn: int, said: str) -> None:
        started = time.perf_counter()
        try:
            quick = await run_in_threadpool(talk.check, self._prompt(session, said, 0, quick=True))
        except Exception as error:
            log.warning("Quick line: %s", type(error).__name__)
            session.note("quick", turn=turn, ms=_ms(started), error=type(error).__name__)
            if turn == session.turn and session.mode == "lead":  # Beside, this may be talk in the room: better silence.
                self._say(session, turn, said, FAILED_LINE, "quick", {})
            return
        timings = {"quick_ms": _ms(started)}
        session.note("quick", turn=turn, ms=timings["quick_ms"], **quick)
        if turn != session.turn or session.ended:
            return
        self._change(session, quick)
        if quick["needs_screen"]:
            if quick["reply"]:
                self._say(session, turn, said, quick, "quick", timings, looks=True)
            session.heard = said
            session.note("look", turn=turn)
            session.publish({"type": "look", "turn": turn})
        elif quick["reply"]:
            self._say(session, turn, said, quick, "quick", timings)
        else:
            session.open_words = ""
            session.publish({"type": "quiet", "turn": turn})

    async def look(self, session: Session, frames: Sequence[Frame]) -> None:
        """The head answers from the screens the phone kept, in both modes. A turn that only chats leaves this answer standing:
        only a newer question to the screen replaces it."""
        said, session.heard = session.heard, ""
        turn = session.turn
        session.looking = turn
        try:
            await self._answer_from(session, turn, said, frames)
        finally:
            if session.looking == turn:
                session.looking = 0

    async def _answer_from(self, session: Session, turn: int, said: str, frames: Sequence[Frame]) -> None:
        started = time.perf_counter()
        newest = frames[-1]
        try:
            answer = await self._see(session, said, frames)
        except Exception as error:
            log.warning("Answer from the screen: %s", type(error).__name__)
            answer = FAILED_LINE
        timings = {"screen_ms": _ms(started)}
        now = time.time() * 1000
        session.note("head", turn=turn, ms=timings["screen_ms"], effort=session.effort or config.META_TALK_REASONING,
                     frame=session.keep_picture(newest, answer["private"]), frames=[frame.timestamp_ms for frame in frames],
                     ages_ms=[round(now - frame.timestamp_ms) for frame in frames], used=session.looking == turn, **answer)
        if session.looking != turn or session.ended or not answer["reply"]:
            return
        self._change(session, answer, newest)
        if answer["show"] and session.moved_at < newest.received_at:  # Over a screen they have left it would only send them back.
            await self.engine.show(session, newest, answer["reply"], answer["show"], answer["private"], timings["screen_ms"])
            if session.looking != turn or session.ended:
                return
        self._say(session, turn, said, answer, "screen", timings)

    async def _see(self, session: Session, said: str, frames: Sequence[Frame]) -> dict:
        prompt = self._prompt(session, said, len(frames))
        if frames[-1].window:
            prompt += window.RULE.format(box=[round(value * 1000) for value in frames[-1].window])
        return await run_in_threadpool(talk.ask, [frame.jpeg for frame in frames], prompt, session.effort)

    def _prompt(self, session: Session, said: str, screens: int, quick: bool = False) -> str:
        last_hint = session.last_hint or {}
        return talk.build_prompt(
            session.goal, [item["summary"] for item in session.history[-HISTORY_STEPS:]],
            last_hint.get("instruction_ru", ""), session.dialog[:session.open_mark], said, screens,
            user_lang=session.user_lang, target_lang=session.app_lang or session.target_lang,
            character=session.character, done=session.completed, quick=quick, mode=session.mode,
        )

    def _change(self, session: Session, answer: dict, frame: Optional[Frame] = None) -> None:
        """A new goal starts the pointer on the newest screen, unless the head has just chosen what to show on it."""
        if answer["mode"]:
            session.set_mode(answer["mode"])
        if answer["new_goal"]:
            session.set_goal(answer["new_goal"])
            frame = frame or session.last_frame
            if session.broadcasting and frame and not answer["show"]:  # A still screen sends no frames.
                self.engine.submit(session, frame)

    def _say(self, session: Session, turn: int, said: str, answer: dict, layer: str, timings: dict,
             looks: bool = False) -> None:
        if turn > session.written_turn:
            session.written_turn = turn
            session.dialog.append({"who": "person", "text": said})
        session.dialog.append({"who": "agent", "text": answer["reply"]})
        session.publish({"type": "say", "turn": turn, "layer": layer, "text_ru": answer["reply"], "goal": answer["new_goal"],
                         "mode": answer["mode"], "looks": looks, "timings": timings})
