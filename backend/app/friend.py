"""The one mind of the call. A turn is their words or a new settled screen, and one model answers it whole."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import re
import time
from typing import Coroutine, List, Optional, Set

from PIL import Image, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

from . import config, grounding, hint, meta, step, window
from .sessions import Frame, Session

log = logging.getLogger(__name__)

ROLE = """Ты на телефонном звонке. Слышишь человека, а когда он показывает экран телефона, видишь снимки. Сам ничего не нажимаешь, нажимает человек.
Ты любознательный исследователь рядом с человеком.
Держи несколько возможных объяснений и путей к цели.
Ищи сведения, которые помогут различить их и выбрать подходящий путь.
Главное уточнение — один вопрос, чей ответ сильнее всего изменит понимание.
Каждое зрительное наблюдение связывай с полученным изображением.
Говори, когда появилось существенное открытие или созрел полезный вопрос.
Остальное время спокойно наблюдай; открытия передавай коротко и по-человечески.
Снимка нет, значит экрана ты не видишь: скажи об этом, даже если человек говорит, что показал.
В одной реплике не больше двух коротких фраз: остальное скажешь на следующем ходу."""
RULES = """Не проси пароли и данные карт. Оплату подтверждает только сам человек.

Серый прямоугольник на экране это твоё окошко: в нём человек видит то, что ты обвёл.

Ответ только JSON:
say: что скажешь вслух, можно ничего.
show: что обвести на последнем экране: label, текст этого места на экране, object, что это, коротко по-английски, и box, [x1, y1, x2, y2] от 0 до 1000 от левого верхнего угла. Обводить нечего, не пиши show.
paste: готовый текст, если человеку надо его вставить. Иначе не пиши paste.
private: true, если на последнем экране оплата, карта, пароль или код.
lang: код языка приложения на последнем экране, две буквы. Экрана нет, не пиши lang.
notes: записи для себя, любой текст. На следующем ходу они вернутся к тебе."""
SILENT_TURN = "(молчит, экран обновился)"
UNSEEN = "(экран не показан)"
FAILED_LINE = "Не получилось ответить, повтори, пожалуйста."

GRAY_SIZE = (64, 96)
FRAME_DIFF_THRESHOLD = 3.0
PIXEL_CHANGED = 12  # A thumbnail pixel counts as changed above this difference.
NEW_SCREEN_SHARE = 0.38  # Measured on a real call: a looping video reaches 0.37, the keyboard 0.33, a new screen 0.4 and up.
SCREENS_KEPT = 4
ANSWER_SECONDS = 15  # Past this the spare model answers: the person is on the line.
LOOK_SECONDS = 2  # The phone sends the screen within this, unless iOS holds it back.
MAX_RESTARTS = 2  # A screen that keeps changing still gets an answer.
MAX_REPLY = 500
MAX_LABEL = 200
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


def _new_screen(session: Session, frame: Frame) -> bool:
    return session.seen_gray is None or changed_share(frame.gray, session.seen_gray) >= NEW_SCREEN_SHARE


def _gone(session: Session, frame: Frame) -> bool:
    """They are on another screen by now. A video on the page is not a reason to take an outline away."""
    return bool(session.last_frame) and changed_share(session.last_frame.gray, frame.gray) >= NEW_SCREEN_SHARE


def _pictures(chat: List[dict]) -> Set[int]:
    """The last few different screens. A new look at the same screen replaces the old one, so the page before it stays."""
    kept: Set[int] = set()
    newest = None
    for index in range(len(chat) - 1, -1, -1):
        frame = chat[index].get("frame")
        if frame is None or (newest and changed_share(frame.gray, newest.gray) < NEW_SCREEN_SHARE):
            continue
        kept.add(index)
        newest = frame
        if len(kept) == SCREENS_KEPT:
            break
    return kept


def messages_of(session: Session) -> List[dict]:
    """A plain chat. The words stay for the whole call, and a turn that was never answered joins the next one."""
    messages: List[dict] = [{"role": "system", "content": ROLE + session.character + "\n\n" + RULES}]
    pictures = _pictures(session.chat)
    for index, turn in enumerate(session.chat):
        if turn["role"] == "assistant":
            messages.append({"role": "assistant", "content": turn["text"]})
            continue
        content = [{"type": "text", "text": turn["text"]}]
        if index in pictures:
            content.append({"type": "image_url", "image_url": {"url": meta.data_url(turn["frame"].jpeg)}})
        if messages[-1]["role"] == "user":
            messages[-1]["content"] += content
        else:
            messages.append({"role": "user", "content": content})
    return messages


def read(text: str) -> dict:
    """The first JSON object of the reply. Whatever else is in there says nothing."""
    answer = json.JSONDecoder().raw_decode(text[text.index("{"):])[0]
    if not isinstance(answer, dict):
        raise ValueError("Некорректный ответ модели.")
    return answer


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _line(value: object) -> str:
    """What goes to the voice. A reply over the limit ends with its last whole sentence."""
    words = _text(value)
    if len(words) <= MAX_REPLY:
        return words
    cut = words[:MAX_REPLY]
    return cut[:max(cut.rfind(mark) for mark in (". ", "? ", "! ")) + 1] or cut


def _target(show: object) -> Optional[dict]:
    """What to outline, in the shape grounding reads. A bad box goes: the label can still find the place. The person is
    waiting for the voice, so the words on the screen are enough even next to a radio button."""
    if not isinstance(show, dict):
        return None
    label, box = _text(show.get("label"))[:MAX_LABEL], show.get("box")
    box = box if isinstance(box, list) and step.good_box(box) else []
    if not label and not box:
        return None
    return {"label": label, "object": _text(show.get("object"))[:MAX_LABEL], "box": box, "mark_id": None, "words_first": True}


def _text_on(session: Session, frame: Frame) -> Optional[list]:
    """The words read on this frame, or on the last one if the screen is still the same. Our window hides what is under it."""
    lines = session.frame_text.get(frame.timestamp_ms)
    last = session.last_frame
    if lines is None and last and same_frame(frame.gray, last.gray):
        lines = session.frame_text.get(last.timestamp_ms)
    return window.outside(lines, frame.window) if lines and frame.window else lines


def _private(session: Session, frame: Optional[Frame], answer: dict) -> bool:
    """The model says so, and the words on the screen can say so without it: this rule does not hang on a single answer."""
    if answer.get("private") is True:
        return True
    target = answer.get("show") if isinstance(answer.get("show"), dict) else {}
    on_screen = " ".join(line["text"] for line in (_text_on(session, frame) or [])) if frame else ""
    return bool(_PRIVATE_SCREEN.search(" ".join((_text(answer.get("say")), _text(target.get("label")), on_screen))))


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


class Friend:
    def __init__(self) -> None:
        self.tasks: Set[asyncio.Task] = set()

    def _run(self, work: Coroutine) -> asyncio.Task:
        task = asyncio.create_task(work)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def heard(self, session: Session, said: str) -> None:
        """Their words. With the screen on, the answer waits for the screen as it is now, which the phone sends at once."""
        if session.open_words:  # They went on before the agent spoke, so both are one line.
            said = session.open_words + " " + said
            del session.chat[session.open_mark:]
        session.open_words = said
        session.open_mark = len(session.chat)
        session.turn += 1
        self._stop(session)
        session.note("heard", turn=session.turn, said=said)
        session.publish({"type": "heard", "turn": session.turn})
        if not session.broadcasting:
            self._start(session, said + " " + UNSEEN, None)
            return
        session.heard = said
        session.publish({"type": "look", "turn": session.turn})
        self._run(self._no_look(session, session.turn))

    async def _no_look(self, session: Session, turn: int) -> None:
        await asyncio.sleep(LOOK_SECONDS)
        if session.heard and session.turn == turn and not session.ended:
            self.look(session, session.last_frame)

    def look(self, session: Session, frame: Optional[Frame]) -> None:
        said, session.heard = session.heard, ""
        if frame and _new_screen(session, frame):
            self._left(session)
        self._start(session, said if frame else said + " " + UNSEEN, frame)

    def screen(self, session: Session, frame: Frame) -> tuple:
        """A frame from the broadcast. Only a new screen is a turn: a video or a banner on the page is not."""
        session.last_frame = frame
        session.touch()
        if session.heard:  # The phone is sending the screen for their words.
            return False, "looking"
        if frame.small:
            return False, "small"
        if not _new_screen(session, frame):
            return False, "same"
        self._left(session)
        if self._busy(session):
            if session.restarts >= MAX_RESTARTS:
                return False, "busy"
            session.restarts += 1
        self._start(session, SILENT_TURN, frame)
        return True, None

    def _left(self, session: Session) -> None:
        """What is outlined belongs to the old screen."""
        if session.shown:
            session.shown = False
            session.publish({"type": "left"})

    def played(self, session: Session, turn: int) -> None:
        if turn == session.turn:
            session.open_words = ""

    def _busy(self, session: Session) -> bool:
        return bool(session.answer and not session.answer.done())

    def _stop(self, session: Session) -> None:
        """The call itself cannot be stopped and ends unused. Its turn stays in the chat, so the next answer covers it."""
        if self._busy(session):
            session.answer.cancel()

    def _start(self, session: Session, text: str, frame: Optional[Frame]) -> None:
        session.chat.append({"role": "user", "text": text, "frame": frame})
        if frame:
            session.seen_gray = frame.gray
        self._stop(session)
        session.answer = self._run(self._answer(session, session.turn, frame))

    async def _ask(self, session: Session) -> Optional[tuple]:
        """No answer in time, or a broken one: one try on the spare model. The person cannot wait for more."""
        messages = messages_of(session)
        effort = session.effort or config.META_TALK_REASONING
        for model in (config.META_TALK_MODEL, config.META_SPARE_MODEL):
            try:
                reply = await asyncio.wait_for(
                    run_in_threadpool(meta.chat_json, model, messages, effort, ANSWER_SECONDS + 1), ANSWER_SECONDS)
                return model, reply, read(reply["text"])
            except Exception as error:
                log.warning("Answer from %s: %s", model, type(error).__name__)
                session.note("no answer", model=model, error=type(error).__name__)
        return None

    async def _answer(self, session: Session, turn: int, frame: Optional[Frame]) -> None:
        started = time.perf_counter()
        got = await self._ask(session)
        session.restarts = 0
        if got is None:
            if session.open_words:
                session.publish({"type": "say", "turn": turn, "text_ru": FAILED_LINE, "paste_text": None, "timings": {"model_ms": _ms(started)}})
            return
        model, reply, answer = got
        ms = _ms(started)
        private = _private(session, frame, answer)
        session.chat.append({"role": "assistant", "text": json.dumps(answer, ensure_ascii=False)})
        session.last_answer = answer
        session.timings = {"model_ms": ms}
        lang = _text(answer.get("lang")).lower()
        if session.target_lang == "auto" and re.fullmatch("[a-z]{2}", lang):
            session.app_lang = lang
        if frame and private:  # It is not sent again, and what the model noted is enough for the next turns.
            for kept in session.chat:
                if kept.get("frame") is frame:
                    kept["frame"] = None
        session.note("answer", turn=turn, model=model, ms=ms, tokens=reply["usage"].get("completion_tokens"), private=private,
                     frame=session.keep_picture(frame, private) if frame else None, **({} if private else {"answer": answer}))
        said, paste = _line(answer.get("say")), _text(answer.get("paste"))
        self._hand_over(session, paste)
        paste_text = paste if session.text_entry == "clipboard" else None
        if not said:  # No sound, so no word from the phone that it has played.
            session.open_words = ""
        if said or paste_text:
            session.publish({"type": "say", "turn": turn, "text_ru": said, "paste_text": paste_text or None, "timings": {"model_ms": ms}})
        else:
            session.publish({"type": "quiet", "turn": turn})
        target = _target(answer.get("show"))
        if target and frame:
            await self._show(session, frame, said or target["label"], target, private, ms)

    def _hand_over(self, session: Session, paste: str) -> None:
        """The keyboard takes the ready text from here."""
        if paste:
            session.step_sequence += 1
            session.step_id = "s" + str(session.step_sequence)
            session.texts = [paste]

    async def _show(self, session: Session, frame: Frame, text: str, target: dict, private: bool, model_ms: int) -> None:
        started = time.perf_counter()
        written = None if private else target
        if _gone(session, frame):  # Over a screen they have left it would only send them back.
            session.note("show", outcome="left", target=written)
            return
        location = await run_in_threadpool(grounding.locate, frame.jpeg, frame.width, frame.height,
                                           {"action": "tap", "target": target}, _text_on(session, frame), [])
        box_ms = _ms(started)
        session.grounding = location
        if not location["box"]:
            session.note("show", outcome="not found", target=written, box_ms=box_ms)
            return
        image = await run_in_threadpool(hint.render, frame.jpeg, location["box"], text, session.eyes)
        session.step_sequence += 1
        session.step_id = "s" + str(session.step_sequence)
        event = {
            "type": "hint", "step_id": session.step_id, "instruction_ru": text,
            "image_jpeg_base64": base64.b64encode(image).decode("ascii"), "frame_ts": frame.timestamp_ms,
            "timings": {"model_ms": model_ms, "box_ms": box_ms, "box_source": location["source"], "window": frame.window is not None},
        }
        if session.eyes:
            event["eyes_box"] = hint.eyes_box(text)
            event["gaze"] = window.gaze(frame.window, location["box"], frame.width, frame.height)
            event["calm"] = private
        session.shown = True
        session.publish(event)
        session.note("show", outcome="shown", step=session.step_id, target=written, box=location["box"],
                     box_source=location["source"], box_ms=box_ms)
