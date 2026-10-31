"""The two layers of the conversation: a quick line from the words alone, then the head, which sees the screens."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Coroutine, Optional, Sequence, Set, Tuple

from starlette.concurrency import run_in_threadpool

from . import config, talk, window
from .engine import HISTORY_STEPS, Engine
from .sessions import Frame, Session

log = logging.getLogger(__name__)

FAILED_LINE = {"reply": "Не получилось ответить, повтори, пожалуйста.", "new_goal": None, "mode": None, "show": None,
               "look_again": False, "private": True, "failed": True}
SETTLES_IN = 2  # Seconds from the start of a move to the frame the phone keeps: it waits 1.5 for a still screen.


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _written_down(answer: dict) -> dict:
    """What a payment or password screen made the head say stays out of the log, as the screen itself does."""
    return {**answer, "reply": None, "show": None} if answer["private"] else answer


class Talker:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.tasks: Set[asyncio.Task] = set()

    def heard(self, session: Session, said: str) -> None:
        """A turn from the stream. Answers run side by side, so a new turn never waits for an old one."""
        self._run(self.answer(session, *self.take(session, said)))

    def _run(self, work: Coroutine) -> None:
        task = asyncio.create_task(work)
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
        session.looks_again = ""
        session.second_look = 0
        session.unspoken = ""
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
        if quick["new_goal"] or quick["mode"]:  # A new plan replaces the question the head is still on.
            session.looking = 0
        if quick["reply"] or quick["needs_screen"]:
            self._hold(session, quick)
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
        only a newer question to the screen or a new plan replaces it."""
        said, session.heard = session.heard, ""
        again, session.second_look = session.second_look, 0
        turn = session.turn
        session.looking = turn
        try:
            await self._answer_from(session, turn, said, frames, again)
        finally:
            if session.looking == turn:
                session.looking = 0

    async def _answer_from(self, session: Session, turn: int, said: str, frames: Sequence[Frame], again: int) -> None:
        started = time.perf_counter()
        newest = frames[-1]
        try:
            answer = await self._see(session, said, frames, again)
        except Exception as error:
            log.warning("Answer from the screen: %s", type(error).__name__)
            answer = FAILED_LINE
        timings = {"screen_ms": _ms(started)}
        now = time.time() * 1000
        session.note("head", turn=turn, ms=timings["screen_ms"], effort=session.effort or config.META_TALK_REASONING,
                     frame=session.keep_picture(newest, answer["private"]), frames=[frame.timestamp_ms for frame in frames],
                     ages_ms=[round(now - frame.timestamp_ms) for frame in frames], used=session.looking == turn,
                     **_written_down(answer))
        if session.looking != turn or session.ended:
            return
        step, session.unspoken = session.unspoken, ""
        if not answer["reply"]:
            self._nothing_new(session, turn, said, step, again)
            return
        self._change(session, answer, newest)
        left = session.moved_at > newest.received_at
        if answer["show"] and not left:  # Over a screen they have left it would only send them back.
            await self.engine.show(session, turn, newest, answer["reply"], answer["show"], answer["private"], timings["screen_ms"])
            if session.looking != turn or session.ended:
                return
        self._say(session, turn, said, answer, "screen", timings)
        if answer["look_again"] and not again and not session.held:
            session.looks_again = said
            if left:
                self.moved(session)

    def _nothing_new(self, session: Session, turn: int, said: str, step: str, again: int) -> None:
        """Right on a screen that is still loading or has not changed, but the person must not be left with silence."""
        if step:
            session.note("step spoken", turn=turn)
            self._say(session, turn, said, {"reply": step, "new_goal": None, "mode": None}, "screen", {})
        elif not again:
            self._say(session, turn, said, FAILED_LINE, "screen", {})
        elif again == 1 and session.mode == "beside":  # Here no pointer speaks up when it has loaded.
            self._run(self._look_again(session, turn, said, 2))

    def _hold(self, session: Session, quick: dict) -> None:
        """After "close", "go back" or "stop" the next step is theirs to say, so the pointer waits for their next words."""
        held, session.held = session.held, quick["hold"] and not quick["new_goal"]  # A new goal is a word to go on.
        if session.held:
            session.revision += 1  # What the pointer is working on belongs to the old plan.
            session.pending_frame = None
            session.note("hold", turn=session.turn)
        elif held and session.broadcasting and session.last_frame and not quick["needs_screen"]:
            session.last_analyzed_gray = None  # A still screen sends no frames.
            self.engine.submit(session, session.last_frame)

    def moved(self, session: Session) -> None:
        """The head said it would look at the screen they were about to open, and it is opening."""
        said, session.looks_again = session.looks_again, ""
        if said:
            self._run(self._look_again(session, session.turn, said))

    async def _look_again(self, session: Session, turn: int, said: str, tries: int = 1) -> None:
        await asyncio.sleep(SETTLES_IN)
        if turn == session.turn and not session.ended:
            session.heard = said
            session.second_look = tries
            session.note("look", turn=turn, again=True)
            session.publish({"type": "look", "turn": turn})

    async def _see(self, session: Session, said: str, frames: Sequence[Frame], again: int = 0) -> dict:
        prompt = self._prompt(session, said, len(frames))
        if frames[-1].window:
            prompt += window.RULE.format(box=[round(value * 1000) for value in frames[-1].window])
        if again:
            prompt += talk.AGAIN_RULE
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
        if layer == "screen":
            session.answered_at = time.perf_counter()
        session.publish({"type": "say", "turn": turn, "layer": layer, "text_ru": answer["reply"], "goal": answer["new_goal"],
                         "mode": answer["mode"], "looks": looks, "wait": answer.get("wait"), "timings": timings})
