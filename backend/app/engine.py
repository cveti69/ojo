"""One analysis per session, with a single slot for the newest pending frame."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import re
import time
import uuid
from pathlib import Path
from typing import Optional

from PIL import Image, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

from . import config, grounding, hint, step
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
SAME_TARGET_DISTANCE = 0.03
MAX_RESTARTS = 4  # A screen that never stops moving still gets a hint.
_LANGUAGE = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$", re.I)
_SCREEN_LANGUAGE = re.compile(r"^\s*\[lang=([^\]]+)\]", re.I)


def prepare_frame(jpeg: bytes, source_width: int, source_height: int, timestamp_ms: int,
                  received_at: float) -> Frame:
    try:
        with Image.open(io.BytesIO(jpeg)) as image:
            if image.format != "JPEG":
                raise ValueError("Нужен кадр JPEG.")
            width, height = image.size
            if width * height > config.MAX_FRAME_PIXELS:
                raise ValueError("Слишком большой размер кадра.")
            image.load()
            gray = image.convert("L").resize(GRAY_SIZE, Image.Resampling.BILINEAR).tobytes()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError("Не удалось прочитать JPEG.") from None
    return Frame(
        jpeg=jpeg, width=width, height=height, source_width=source_width, source_height=source_height,
        timestamp_ms=timestamp_ms, gray=gray, digest=hashlib.sha256(gray).hexdigest(),
        received_at=received_at, receive_ms=round((time.perf_counter() - received_at) * 1000),
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


def outdated(session: Session, revision: int) -> bool:
    return session.ended or revision != session.revision or session.stale.is_set()


def same_step(last: Optional[dict], frame: Frame, action: str, box: Optional[list], confirm: bool) -> bool:
    """Our own hint changes the frame and the model rewords itself, so only the target and a new screen count."""
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


def _save_frame(frame: Frame, session_id: str) -> None:
    directory = config.FRAMES_DIR.expanduser().resolve()
    repository = Path(__file__).resolve().parents[2]
    if directory == repository or repository in directory.parents:
        raise ValueError("FRAMES_DIR должна находиться вне репозитория.")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = directory / (session_id + "_" + str(frame.timestamp_ms) + "_" + uuid.uuid4().hex[:8] + ".jpg")
    with destination.open("xb") as output:
        output.write(frame.jpeg)


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
        session.last_frame = frame
        session.touch()
        if session.busy:
            session.pending_frame = frame
            if left_the_screen(session, frame):  # A hint for the old screen would only send them back.
                session.stale.set()
            return False, "busy"
        if session.needs_user_confirm:
            return False, "user_confirm"
        if session.completed:
            return False, "done"
        if same_frame(frame.gray, session.last_analyzed_gray):
            return False, "unchanged"
        session.busy = True
        session.task = asyncio.create_task(self._drain(session, frame))
        return True, None

    async def _drain(self, session: Session, frame: Frame) -> None:
        try:
            while not session.ended:
                if session.needs_user_confirm or session.completed:
                    break
                if not same_frame(frame.gray, session.last_analyzed_gray):
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
            "frame_ts": frame.timestamp_ms,
        }
        session.timings = timings
        session.analyzing_gray = frame.gray
        session.stale.clear()
        session.publish({
            "type": "status", "state": "analyzing", "text_ru": "Агент думает",
            "new_screen": bool(session.last_step) and changed_share(frame.gray, session.last_step["gray"]) >= MOVED_ON_SHARE,
        })
        if config.SAVE_FRAMES:
            await run_in_threadpool(_save_frame, frame, session.id)
        if outdated(session, revision):
            return
        prompt = step.build_prompt(
            session.goal, [item["summary"] for item in session.history[-HISTORY_STEPS:]],
            user_lang=session.user_lang, target_lang=session.app_lang or session.target_lang,
            character=session.character,
        )
        if session.target_lang == "auto":
            prompt += (
                "\nDetect the app language from the visible interface. Prefix the screen field with "
                "[lang=CODE], using its BCP-47 language code, e.g. [lang=id]. "
                "Use [lang=unknown] if the language cannot be identified. Keep the JSON schema."
            )
        icons = asyncio.ensure_future(find_icons(frame))
        try:
            await self._next_step(session, frame, revision, prompt, timings, icons)
        finally:
            icons.cancel()
            session.restarts = session.restarts + 1 if session.stale.is_set() else 0

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

    async def _next_step(self, session: Session, frame: Frame, revision: int, prompt: str, timings: dict,
                         icons: asyncio.Future) -> None:
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
        if answer["done"] is True and not needs_confirm:
            session.last_frame_hash = frame.digest
            session.last_analyzed_gray = frame.gray
            session.completed = True
            session.texts = []
            session.insert_hint_ru = ""
            session.publish({"type": "done", "text_ru": DONE_TEXT})
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
                                           session.frame_text.get(frame.timestamp_ms), await ready_icons(icons))
        timings["box_ms"] = round((time.perf_counter() - stage) * 1000)
        if outdated(session, revision):
            return
        session.grounding = location
        if same_step(session.last_step, frame, answer["action"], location["box"], needs_confirm):
            session.last_frame_hash = frame.digest
            session.last_analyzed_gray = frame.gray
            session.publish({"type": "status", "state": "broadcasting" if session.broadcasting else "waiting_frames",
                             "text_ru": "Жду изменения экрана"})
            timings["total_ms"] = round((time.perf_counter() - frame.received_at) * 1000)
            return
        stage = time.perf_counter()
        image = await run_in_threadpool(hint.render, frame.jpeg, location["box"], instruction)
        timings["image_ms"] = round((time.perf_counter() - stage) * 1000)
        if outdated(session, revision):
            return
        session.step_sequence += 1
        step_id = "s" + str(session.step_sequence)
        event = {
            "type": "hint", "step_id": step_id, "instruction_ru": instruction, "speak": True,
            "image_jpeg_base64": base64.b64encode(image).decode("ascii"),
            "insert_texts": texts, "insert_hint_ru": answer["insert_hint_ru"] if texts else "",
            "paste_text": paste_text, "needs_user_confirm": needs_confirm, "frame_ts": frame.timestamp_ms,
            "timings": {"queue_ms": timings["queue_ms"], "model_ms": timings["model_ms"], "box_ms": timings["box_ms"],
                        "box_source": location["source"]},
        }
        session.step_id = step_id
        session.texts = texts
        session.insert_hint_ru = event["insert_hint_ru"]
        session.last_step = {"action": answer["action"], "box": location["box"], "confirm": needs_confirm,
                             "gray": frame.gray}
        session.last_frame_hash = frame.digest
        session.last_analyzed_gray = frame.gray
        session.needs_user_confirm = needs_confirm
        session.history.append({
            "id": step_id, "instruction_ru": instruction, "target": location["box"], "time": time.time(),
            "summary": remembered_step(instruction, answer["action"], texts),
        })
        stage = time.perf_counter()
        session.publish(event)
        timings["send_ms"] = round((time.perf_counter() - stage) * 1000)
        timings["total_ms"] = round((time.perf_counter() - frame.received_at) * 1000)
