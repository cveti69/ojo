"""One analysis per session, with a single slot for the newest pending frame."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import re
import time
from typing import Optional, Sequence

from PIL import Image, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

from . import config, grounding, hint, step, window
from .sessions import Frame, Session

GRAY_SIZE = (64, 96)
FRAME_DIFF_THRESHOLD = 3.0
PAYMENT_INSTRUCTION = "Проверь сумму и подтверди сам."
PASTE_INSTRUCTION = "Зажми поле, выбери «Вставить» или набери: {text}"
DONE_TEXT = "Готово, цель достигнута"
ICON_WAIT_SECONDS = 1.5
HISTORY_STEPS = 12
PIXEL_CHANGED = 12  # A thumbnail pixel counts as changed above this difference.
NEW_SCREEN_SHARE = 0.4  # Our own floating window covers less of the frame than this, a new screen covers more.
MOVED_ON_SHARE = 0.25  # Our window never changes this much of the frame, the keyboard or a new screen does.
MOVED_ON_SHARE_WITH_EYES = 0.3  # Measured on public pages: a banner or a video reaches 0.29, the keyboard 0.33, a new screen 0.63.
SAME_TARGET_DISTANCE = 0.03
MAX_RESTARTS = 4  # A screen that never stops moving still gets a hint.
_LANGUAGE = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$", re.I)
_SCREEN_LANGUAGE = re.compile(r"^\s*\[lang=([^\]]+)\]", re.I)
_PRIVATE_SCREEN = re.compile(r"парол|\bкарт[аыуеой]|пин-код|password|\bpin\b|cvv|cvc|card number|credit card|kata sandi|nomor kartu", re.I)


def prepare_frame(jpeg: bytes, source_width: int, source_height: int, timestamp_ms: int,
                  received_at: float, small: bool = False) -> Frame:
    """From here on our own window is a grey patch, so its changing picture never counts as a new screen."""
    try:
        with Image.open(io.BytesIO(jpeg)) as image:
            if image.format != "JPEG":
                raise ValueError("Нужен кадр JPEG.")
            width, height = image.size
            if width * height > config.MAX_FRAME_PIXELS:
                raise ValueError("Слишком большой размер кадра.")
        found = window.find(jpeg)
        if found:
            jpeg = window.hide(jpeg, found)
        with Image.open(io.BytesIO(jpeg)) as image:
            gray = image.convert("L").resize(GRAY_SIZE, Image.Resampling.BILINEAR).tobytes()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError("Не удалось прочитать JPEG.") from None
    return Frame(
        jpeg=jpeg, width=width, height=height, source_width=source_width, source_height=source_height,
        timestamp_ms=timestamp_ms, gray=gray, digest=hashlib.sha256(gray).hexdigest(),
        received_at=received_at, receive_ms=round((time.perf_counter() - received_at) * 1000), window=found, small=small,
    )


def same_frame(gray: bytes, previous: Optional[bytes]) -> bool:
    if previous is None or len(gray) != len(previous):
        return False
    return sum(abs(a - b) for a, b in zip(gray, previous)) / len(gray) <= FRAME_DIFF_THRESHOLD


def changed_share(gray: bytes, previous: bytes) -> float:
    return sum(abs(a - b) > PIXEL_CHANGED for a, b in zip(gray, previous)) / len(gray)


def left_the_screen(session: Session, frame: Frame) -> bool:
    return (session.restarts < MAX_RESTARTS and session.analyzing_gray is not None
            and changed_share(frame.gray, session.analyzing_gray) >= MOVED_ON_SHARE)


def left_the_payment(session: Session, frame: Frame) -> bool:
    """The agent stops on the payment screen and on no other: on a new one it is needed again."""
    return not session.last_step or changed_share(frame.gray, session.last_step["gray"]) >= NEW_SCREEN_SHARE


def outdated(session: Session, revision: int) -> bool:
    return session.ended or revision != session.revision or session.stale.is_set()


def same_step(last: Optional[dict], frame: Frame, action: str, box: Optional[list], confirm: bool) -> bool:
    """Our own hint changes the frame and the model rewords itself, so only the target and a new screen count."""
    if last and last["confirm"] and confirm:  # Said once, and they may well scroll the payment screen to check it.
        return True
    if not last or last["action"] != action or last["confirm"] != confirm:
        return False
    if changed_share(frame.gray, last["gray"]) >= NEW_SCREEN_SHARE:
        return False
    if not last["box"] or not box:
        return last["box"] == box
    return all(abs(a - b) <= SAME_TARGET_DISTANCE
               for a, b in zip(grounding.center(last["box"]), grounding.center(box)))


def remembered_step(instruction: str, action: str, texts: list) -> str:
    """What the model reads as history. Typing is spelled out so it knows the text in a field is ours."""
    return 'typed "' + texts[0] + '" into the field' if action == "type" and texts else instruction


async def find_icons(frame: Frame) -> list:
    """An upgrade for icon targets: a failure here must not cost the hint."""
    try:
        return await run_in_threadpool(grounding.find_icons, frame.jpeg, frame.width, frame.height)
    except Exception:
        return []


async def ready_icons(icons: asyncio.Future) -> list:
    """The model usually finishes last. If it did not, the hint waits only a moment for the icons."""
    try:
        return await asyncio.wait_for(asyncio.shield(icons), ICON_WAIT_SECONDS)
    except asyncio.TimeoutError:
        return []


def _text_on(session: Session, frame: Frame) -> Optional[list]:
    """The words read on this frame, or on the last one if the screen is still the same. Our window hides what is under it."""
    lines = session.frame_text.get(frame.timestamp_ms)
    last = session.last_frame
    if lines is None and last and same_frame(frame.gray, last.gray):
        lines = session.frame_text.get(last.timestamp_ms)
    return window.outside(lines, frame.window) if lines and frame.window else lines


def _detected_language(answer: dict) -> Optional[str]:
    match = _SCREEN_LANGUAGE.match(answer.get("screen", ""))
    candidates = [answer.get("app_lang"), answer.get("screen_lang"), answer.get("target_lang")]
    if match:
        candidates.insert(0, match.group(1))
    for candidate in candidates:
        if (isinstance(candidate, str) and _LANGUAGE.fullmatch(candidate.strip())
                and candidate.lower() not in ("auto", "und")):
            return candidate.strip()
    return None


class Engine:
    def submit(self, session: Session, frame: Frame) -> tuple:
        if session.mode == "beside":
            return False, "beside"
        session.last_frame = frame
        session.touch()
        if session.held:
            return False, "held"
        if session.busy:
            session.pending_frame = frame
            if left_the_screen(session, frame):  # A hint for the old screen would only send them back.
                session.stale.set()
            return False, "busy"
        if session.needs_user_confirm:
            if not left_the_payment(session, frame):
                return False, "user_confirm"
            session.needs_user_confirm = False
        if session.completed:
            return False, "done"
        if same_frame(frame.gray, session.last_analyzed_gray) and not frame.small:
            if session.last_status["state"] == "analyzing":  # It moved and came back to the same screen.
                session.publish({"type": "status", "state": "broadcasting", "text_ru": "Жду изменения экрана"})
            return False, "unchanged"
        session.busy = True
        session.task = asyncio.create_task(self._drain(session, frame))
        return True, None

    def moved(self, session: Session) -> None:
        """The screen started to change, and frames wait until it stops. A hint for the old one would only send them back."""
        session.moved_at = time.perf_counter()
        if session.mode == "beside" and session.shown:
            session.shown = False
            session.publish({"type": "left"})
        if session.mode != "lead" or session.completed or session.needs_user_confirm or not session.last_step:
            return
        if session.busy and session.restarts < MAX_RESTARTS:
            session.pending_frame = None
            session.stale.set()
        session.publish({"type": "status", "state": "analyzing", "text_ru": "Агент думает", "new_screen": True})

    async def _drain(self, session: Session, frame: Frame) -> None:
        try:
            while not session.ended:
                if session.needs_user_confirm or session.completed:
                    break
                if frame.small or not same_frame(frame.gray, session.last_analyzed_gray):
                    revision = session.revision
                    try:
                        await self._analyze(session, frame, revision)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        if not session.ended and revision == session.revision:
                            session.publish({"type": "status", "state": "error",
                                             "text_ru": "Не удалось разобрать экран. Пришли следующий кадр."})
                    finally:
                        if revision == session.revision:
                            session.timings["total_ms"] = round((time.perf_counter() - frame.received_at) * 1000)
                pending = session.pending_frame
                session.pending_frame = None
                if pending is None:
                    break
                frame = pending
        finally:
            session.pending_frame = None
            session.busy = False
            session.task = None
            if not session.ended and session.last_status["state"] == "analyzing":
                session.publish({
                    "type": "status", "state": "broadcasting" if session.broadcasting else "waiting_frames",
                    "text_ru": (PAYMENT_INSTRUCTION if session.needs_user_confirm
                                else DONE_TEXT if session.completed else "Жду изменения экрана"),
                })

    async def _analyze(self, session: Session, frame: Frame, revision: int) -> None:
        started = time.perf_counter()
        timings = {
            "receive_ms": frame.receive_ms, "model_ms": 0, "box_ms": 0, "image_ms": 0, "send_ms": 0,
            "queue_ms": round((started - frame.received_at) * 1000) - frame.receive_ms,
            "frame_ts": frame.timestamp_ms, "age_ms": round(time.time() * 1000) - frame.timestamp_ms,
        }
        session.timings = timings
        session.analyzing_gray = frame.gray
        session.stale.clear()
        session.publish({
            "type": "status", "state": "analyzing", "text_ru": "Агент думает",
            "new_screen": bool(session.last_step) and changed_share(frame.gray, session.last_step["gray"]) >= (
                MOVED_ON_SHARE_WITH_EYES if session.eyes else MOVED_ON_SHARE),
        })
        if outdated(session, revision):
            return
        prompt = step.build_prompt(
            session.goal, [item["summary"] for item in session.history[-HISTORY_STEPS:]],
            user_lang=session.user_lang, target_lang=session.app_lang or session.target_lang,
            character=session.character, dialog=session.talk_so_far(),
        )
        if frame.window:
            prompt += window.RULE.format(box=[round(value * 1000) for value in frame.window])
        if session.target_lang == "auto":
            prompt += (
                "\nDetect the app language from the visible interface. Prefix the screen field with "
                "[lang=CODE], using its BCP-47 language code, e.g. [lang=id]. "
                "Use [lang=unknown] if the language cannot be identified. Keep the JSON schema."
            )
        icons = asyncio.ensure_future(find_icons(frame))
        seen = {"outcome": "dropped"}
        try:
            await self._next_step(session, frame, revision, prompt, timings, icons, seen)
        except Exception as error:
            seen["outcome"] = "failed: " + type(error).__name__
            raise
        finally:
            icons.cancel()
            session.restarts = session.restarts + 1 if session.stale.is_set() else 0
            session.note("step", frame_ts=frame.timestamp_ms, age_ms=timings["age_ms"], model_ms=timings["model_ms"],
                         box_ms=timings["box_ms"], window=frame.window is not None, **seen)

    async def _ask(self, session: Session, frame: Frame, prompt: str) -> Optional[dict]:
        """Stops waiting once the person leaves the screen. The call itself cannot be stopped and ends unused."""
        model = asyncio.ensure_future(run_in_threadpool(step.ask, frame.jpeg, prompt, effort=session.effort))
        stale = asyncio.ensure_future(session.stale.wait())
        try:
            await asyncio.wait((model, stale), return_when=asyncio.FIRST_COMPLETED)
        finally:
            stale.cancel()
            model.cancel()
        return model.result() if model.done() else None

    async def show(self, session: Session, turn: int, frame: Frame, text: str, target: dict, private: bool,
                   model_ms: int) -> None:
        """What the head chose goes up the way a step does, and it beats the plan: an analysis under way belongs to the old
        plan, and this screen now counts as seen."""
        session.revision += 1
        started = time.perf_counter()
        written = None if private else target
        location = await run_in_threadpool(grounding.locate, frame.jpeg, frame.width, frame.height,
                                           {"action": "tap", "target": target}, _text_on(session, frame), [])
        box_ms = round((time.perf_counter() - started) * 1000)
        if session.ended or not location["box"]:
            session.note("show", outcome="not found", target=written, box_ms=box_ms)
            return
        image = await run_in_threadpool(hint.render, frame.jpeg, location["box"], text, session.eyes)
        if session.ended or session.looking != turn or session.moved_at > frame.received_at:
            session.note("show", outcome="late", target=written, box_ms=box_ms)  # They went on while the button was found.
            return
        timings = {"queue_ms": 0, "model_ms": model_ms, "box_ms": box_ms, "box_source": location["source"],
                   "window": frame.window is not None}
        step_id = self._put_up(session, frame, image, location, "tap", text, speak=False, calm=private, timings=timings)
        session.note("show", outcome="shown", step=step_id, target=written, box=location["box"], box_source=location["source"],
                     box_ms=box_ms)

    def _put_up(self, session: Session, frame: Frame, image: bytes, location: dict, action: str, instruction: str,
                speak: bool = True, texts: Sequence[str] = (), insert_hint: str = "", paste_text: Optional[str] = None,
                confirm: bool = False, calm: bool = False, timings: Optional[dict] = None) -> str:
        """The one way a hint reaches the window. Beside the person nothing of the screen stays here."""
        session.step_sequence += 1
        step_id = "s" + str(session.step_sequence)
        event = {
            "type": "hint", "step_id": step_id, "instruction_ru": instruction, "speak": speak,
            "image_jpeg_base64": base64.b64encode(image).decode("ascii"),
            "insert_texts": list(texts), "insert_hint_ru": insert_hint if texts else "",
            "paste_text": paste_text, "needs_user_confirm": confirm, "frame_ts": frame.timestamp_ms, "timings": timings,
        }
        if session.eyes:
            event["eyes_box"] = hint.eyes_box(instruction)
            event["gaze"] = window.gaze(frame.window, location["box"], frame.width, frame.height)
            event["calm"] = calm
        session.step_id = step_id
        session.texts = list(texts)
        session.insert_hint_ru = event["insert_hint_ru"]
        session.history.append({
            "id": step_id, "instruction_ru": instruction, "target": location["box"], "time": time.time(),
            "summary": remembered_step(instruction, action, list(texts)),
        })
        session.publish(event)
        if session.mode == "beside":
            session.last_hint = None
            session.shown = True
        else:
            session.last_step = {"action": action, "box": location["box"], "confirm": confirm, "gray": frame.gray}
            session.last_frame_hash = frame.digest
            session.last_analyzed_gray = frame.gray
        return step_id

    async def _next_step(self, session: Session, frame: Frame, revision: int, prompt: str, timings: dict,
                         icons: asyncio.Future, seen: dict) -> None:
        stage = time.perf_counter()
        result = await self._ask(session, frame, prompt)
        timings["model_ms"] = round((time.perf_counter() - stage) * 1000)
        if outdated(session, revision):
            return
        answer = result.get("answer")
        if not isinstance(answer, dict) or step.check(answer):
            raise ValueError("Некорректный ответ модели.")
        session.last_answer = answer
        language = _detected_language(answer)
        if language and session.target_lang == "auto":
            session.app_lang = language
        needs_confirm = answer["needs_user_confirm"] is True
        private = needs_confirm or bool(_PRIVATE_SCREEN.search(answer["screen"] + " " + answer["instruction"]))
        seen.update(frame=session.keep_picture(frame, private), private=private, action=answer["action"], confirm=needs_confirm,
                    done=answer["done"], **({} if private else {key: answer[key] for key in ("screen", "instruction", "target")}))
        if answer["done"] is True and not needs_confirm:
            session.last_frame_hash = frame.digest
            session.last_analyzed_gray = frame.gray
            session.completed = True
            session.texts = []
            session.insert_hint_ru = ""
            session.publish({"type": "done", "text_ru": answer["instruction"].strip() or DONE_TEXT})
            seen["outcome"] = "done"
            timings["total_ms"] = round((time.perf_counter() - frame.received_at) * 1000)
            return
        texts = [value for value in answer["insert_texts"] if isinstance(value, str) and value.strip()]
        instruction = answer["instruction"]
        paste_text = None
        if needs_confirm:
            instruction = PAYMENT_INSTRUCTION
            texts = []
        elif session.text_entry == "clipboard" and answer["action"] == "type" and texts:
            instruction = PASTE_INSTRUCTION.format(text=texts[0])
            paste_text = texts[0]
        stage = time.perf_counter()
        location = await run_in_threadpool(grounding.locate, frame.jpeg, frame.width, frame.height, answer,
                                           _text_on(session, frame), await ready_icons(icons))
        timings["box_ms"] = round((time.perf_counter() - stage) * 1000)
        if outdated(session, revision):
            return
        session.grounding = location
        seen.update(box=location["box"], box_source=location["source"])
        if same_step(session.last_step, frame, answer["action"], location["box"], needs_confirm):
            session.needs_user_confirm = needs_confirm
            session.last_frame_hash = frame.digest
            session.last_analyzed_gray = frame.gray
            session.publish({"type": "status", "state": "broadcasting" if session.broadcasting else "waiting_frames",
                             "text_ru": "Жду изменения экрана"})
            seen["outcome"] = "same step"
            timings["total_ms"] = round((time.perf_counter() - frame.received_at) * 1000)
            return
        stage = time.perf_counter()
        image = await run_in_threadpool(hint.render, frame.jpeg, location["box"], instruction, session.eyes)
        timings["image_ms"] = round((time.perf_counter() - stage) * 1000)
        if outdated(session, revision):
            return
        stage = time.perf_counter()
        session.needs_user_confirm = needs_confirm
        thinking = bool(session.heard or session.looking)  # The answer they are waiting for goes first.
        speak = not thinking and session.answered_at <= frame.received_at
        session.unspoken = instruction if thinking else ""
        step_id = self._put_up(session, frame, image, location, answer["action"], instruction, speak=speak,
                               texts=texts, insert_hint=answer["insert_hint_ru"], paste_text=paste_text,
                               confirm=needs_confirm, calm=private,
                               timings={"queue_ms": timings["queue_ms"], "model_ms": timings["model_ms"], "box_ms": timings["box_ms"],
                                        "box_source": location["source"], "window": frame.window is not None})
        seen.update(outcome="hint", step=step_id, said=None if private else instruction, spoken=speak)
        timings["send_ms"] = round((time.perf_counter() - stage) * 1000)
        timings["total_ms"] = round((time.perf_counter() - frame.received_at) * 1000)
