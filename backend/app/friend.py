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
from typing import Callable, Coroutine, List, Optional, Set

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
Новый экран сам по себе не вопрос к тебе. Если человек ничего не спросил, говори, только когда твои слова изменят его следующий шаг.
Остальное время спокойно наблюдай; открытия передавай коротко и по-человечески.
Если снимка нет, не говори, что видишь экран.
Обычно одна короткая фраза. Вторая, только если без неё человек ошибётся."""
RULES = """Не проси пароли и данные карт. Оплату подтверждает только сам человек.

Серый прямоугольник на экране это твоё окошко: в нём человек видит то, что ты обвёл.

Ответ только JSON:
say: что скажешь вслух, можно ничего.
show: что обвести на последнем экране: label, текст этого места дословно, как на экране, на языке экрана, object, что это, коротко по-английски, и box, [x1, y1, x2, y2] от 0 до 1000 от левого верхнего угла. Обводить нечего, не пиши show.
paste: готовый текст, если человеку надо его вставить. Иначе не пиши paste.
private: true, если на последнем экране оплата, карта, пароль или код.
lang: код языка приложения на последнем экране, две буквы. Экрана нет, не пиши lang.
notes: записи для себя, любой текст. На следующем ходу они вернутся к тебе."""
FACES = ("думаю", "нашёл", "сомневаюсь", "рад", "стоп")
FACE_RULE = "\nface: какое у тебя сейчас лицо, одно слово: " + ", ".join(FACES) + "."
SILENT_TURN = "(молчит, экран обновился)"
UNSEEN = "(экран не показан)"
UNSHOWN = "(обводка не показана)"
FAILED_LINE = "Не получилось ответить, повтори, пожалуйста."

GRAY_SIZE = (64, 96)
FRAME_DIFF_THRESHOLD = 3.0
PIXEL_CHANGED = 12  # A thumbnail pixel counts as changed above this difference.
NEW_SCREEN_SHARE = 0.38  # Measured on a real call: a looping video reaches 0.37, the keyboard 0.33, a new screen 0.4 and up.
SCREENS_KEPT = 4
SPARE_SECONDS = 8  # With no line by then the spare model starts beside the main one: on real calls 2 lines of 113 came later.
ANSWER_SECONDS = 30  # One limit for both, so no call lives for ever. The eyes on the phone wait 35 s with the look.
LOOK_SECONDS = 2  # The phone sends the screen within this, unless iOS holds it back.
LATE_SECONDS = 10  # The phone gives up on a frame after 5 s, so anything older means its clock has moved.
MAX_RESTARTS = 2  # A screen that keeps changing still gets an answer.
MAX_REPLY = 500
MAX_LABEL = 200
_SAY_FIRST = re.compile(r'\s*\{\s*"say"\s*:\s*"((?:[^"\\]|\\.)*)"', re.S)
# Broad on purpose: a screen wrongly taken for a payment costs one picture in the log, the other way round costs a card number.
_PAYMENT = (
    r"парол|\bкартой\b|пин-код|оплат|заплат|плат[её]ж|код подтверждения|код из смс"
    r"|password|\bpin\b|cvv|cvc|(credit|debit|bank) card|card (number|details)|payment|billing|\bpay\b|verification code|\botp\b"
    r"|sandi|kartu|bayar|rekening|transfer bank|kode verifikasi"
    r"|支付|付款|密码|银行卡|验证码|パスワード|クレジットカード|支払|認証コード|비밀번호|결제|카드 ?번호|인증번호|รหัสผ่าน|ชำระเงิน|บัตรเครดิต|รหัสยืนยัน|mật khẩu|thanh toán|số thẻ|mã xác|كلمة المرور|الدفع|رقم البطاقة|رمز التحقق")
_PRIVATE_SCREEN = re.compile(_PAYMENT + r"|\bкарт[аыуе]\b", re.I)
# The model can name a map app, so in its own words "карты" alone is not a payment.
_PRIVATE_WORDS = re.compile(_PAYMENT + r"|(номер|данн|банковск|кредитн|дебетов|действия)\w*\s+(\w+\s+)?карт(а|ы|у|е|ой)?\b", re.I)


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


def _ahead(session: Session, frame: Frame) -> bool:
    """A look shows the screen as it is now, so what follows is measured against it. The same screen keeps the frame that has its words."""
    last = session.last_frame
    return not last or (frame.timestamp_ms > last.timestamp_ms and not same_frame(frame.gray, last.gray))


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
    rules = RULES + FACE_RULE if session.eyes else RULES
    messages: List[dict] = [{"role": "system", "content": ROLE + session.character + "\n\n" + rules}]
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


def _plain(text: str) -> str:
    return re.sub(r"\W+", " ", text).strip().lower()


def first_line(text: str) -> str:
    """The spoken line of an answer still being written, once it is whole. The model writes it first, and an answer
    that does not simply waits for its end."""
    found = _SAY_FIRST.match(text)
    if not found:
        return ""
    try:
        return _line(json.loads('"' + found.group(1) + '"', strict=False))
    except ValueError:
        return ""


def _private(session: Session, frame: Optional[Frame], answer: dict) -> bool:
    """The model says so, and the words on the screen can say so without it: this rule does not hang on a single answer."""
    if answer.get("private") is True:
        return True
    target = answer.get("show") if isinstance(answer.get("show"), dict) else {}
    said = " ".join((_text(answer.get("say")), _text(target.get("label")), _text(target.get("object"))))
    on_screen = " ".join(line["text"] for line in (_text_on(session, frame) or [])) if frame else ""
    return bool(_PRIVATE_WORDS.search(said) or _PRIVATE_SCREEN.search(on_screen))


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
        if _plain(session.open_words).endswith(_plain(said)):  # The same phrase by its second road, the file after the stream.
            return
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
        if frame and _ahead(session, frame):
            session.last_frame = frame
        self._start(session, said if frame else said + " " + UNSEEN, frame)

    def screen(self, session: Session, frame: Frame) -> tuple:
        """A frame from the broadcast. Only a new screen is a turn: a video or a banner on the page is not."""
        session.touch()
        last = session.last_frame
        if last and 0 < last.timestamp_ms - frame.timestamp_ms < LATE_SECONDS * 1000:  # A look or the next frame got here first.
            return False, "old"
        session.last_frame = frame
        if session.heard:  # The phone is sending the screen for their words.
            return False, "looking"
        if frame.small:
            return False, "small"
        if not _new_screen(session, frame):
            return False, "same"
        self._left(session)
        if session.eyes:
            session.publish({"type": "reading", "turn": session.turn})
        if self._busy(session):
            if session.restarts >= MAX_RESTARTS:
                session.held = True
                return False, "busy"
            session.restarts += 1
        self._start(session, SILENT_TURN, frame)
        return True, None

    def _left(self, session: Session) -> None:
        """What is outlined belongs to the old screen."""
        if session.shown:
            session.shown = False
            session.last_hint = None  # A phone that reconnects or asks for the hint must not get it back.
            session.publish({"type": "left"})

    def text_read(self, session: Session, timestamp_ms: int) -> None:
        """The words on a screen can come after its answer, and a payment screen leaves the log and the chat all the same.
        A frame not known here is ahead, and its answer reads these words with our window taken out, or it went with a turn
        they talked over, and then only its picture can be left to take away. A wrong guess costs that picture and nothing else."""
        known = [session.last_frame] + [kept.get("frame") for kept in session.chat]
        frame = next((seen for seen in known if seen and seen.timestamp_ms == timestamp_ms), None)
        lines = (_text_on(session, frame) if frame else session.frame_text.get(timestamp_ms)) or []
        if not _PRIVATE_SCREEN.search(" ".join(line["text"] for line in lines)):
            return
        if session.call_log:
            session.call_log.forget(timestamp_ms)
        if not frame:
            return
        for kept in session.chat:
            if kept.get("frame") and kept["frame"].timestamp_ms == timestamp_ms:
                kept["frame"] = None
        if session.eyes:
            if session.last_hint and session.last_hint["frame_ts"] == timestamp_ms:
                session.last_hint["calm"] = True  # A phone that reconnects gets it again.
            session.publish({"type": "face", "turn": session.turn, "face": None, "calm": True})

    def played(self, session: Session, turn: int) -> None:
        if turn == session.turn:
            session.open_words = ""

    def _busy(self, session: Session) -> bool:
        return bool(session.answer and not session.answer.done())

    def _stop(self, session: Session) -> None:
        """The call itself cannot be stopped and ends unused. Its turn stays in the chat, so the next answer covers it."""
        if self._busy(session):
            session.answer.cancel()
            session.answer = None

    def _start(self, session: Session, text: str, frame: Optional[Frame]) -> None:
        session.chat.append({"role": "user", "text": text, "frame": frame})
        if frame:
            session.seen_gray = frame.gray
        self._stop(session)
        session.answer = self._run(self._answer(session, session.turn, frame))

    def _on_text(self, session: Session, turn: int, started: float, early: dict, model: str) -> tuple:
        """The line goes to the voice once it is whole, ahead of the rest of the answer. It reaches the chat at the same
        moment, because the rest may never come. The call runs on in its thread after a new turn, which then has the say,
        and after the other model won: the second thing returned is what shuts this one up."""
        loop, task = asyncio.get_running_loop(), asyncio.current_task()
        found: List[str] = []
        dropped: List[bool] = []

        def deliver(line: str) -> None:
            if session.answer is not task or session.ended or early or dropped:
                return
            early.update(line=line, ms=_ms(started), model=model, turn={"role": "assistant", "text": json.dumps({"say": line}, ensure_ascii=False)})
            session.chat.append(early["turn"])
            session.publish({"type": "say", "turn": turn, "text_ru": line, "paste_text": None, "timings": {"model_ms": early["ms"]}})

        def on_text(text: str) -> None:
            if not found and first_line(text):
                found.append(first_line(text))
                loop.call_soon_threadsafe(deliver, found[0])
        return on_text, lambda: dropped.append(True)

    async def _call(self, session: Session, model: str, messages: List[dict], effort: str, seconds: float, on_text: Callable) -> Optional[tuple]:
        try:
            reply = await asyncio.wait_for(run_in_threadpool(meta.chat_json, model, messages, effort, seconds + 1, on_text), seconds)
            return model, reply, read(reply["text"])
        except Exception as error:
            log.warning("Answer from %s: %s", model, type(error).__name__)
            session.note("no answer", model=model, error=type(error).__name__)
            return None

    async def _ask(self, session: Session, turn: int, started: float, early: dict) -> Optional[tuple]:
        """The main model alone while the person can wait. With no line from it by then, or a broken answer, the spare model
        runs beside it. The first whole line has the say and the other call ends unused, but silence from the spare one
        waits for the main one. A line that has already gone out stands, and only the rest of the answer is lost."""
        messages = messages_of(session)
        effort = session.effort or config.META_TALK_REASONING
        main, spare = config.META_TALK_MODEL, config.META_SPARE_MODEL
        running: dict = {}
        ended: dict = {}
        drops: List[Callable] = []

        def call(model: str) -> asyncio.Task:
            on_text, drop = self._on_text(session, turn, started, early, model)
            drops.append(drop)
            seconds = ANSWER_SECONDS - (time.perf_counter() - started)
            task = self._run(self._call(session, model, messages, effort, seconds, on_text))
            running[task] = model
            return task

        try:
            first = call(main)
            await asyncio.wait({first}, timeout=SPARE_SECONDS)
            if not early and not (first.done() and first.result()):
                session.note("spare", model=spare, ms=_ms(started))
                call(spare)
            while running:
                done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                ended.update((running.pop(task), task.result()) for task in done)
                if early:
                    if early["model"] in ended:  # The word on this screen would have come with the rest, so nothing about it is written down.
                        return ended[early["model"]] or (early["model"], {"usage": {}}, {"say": early["line"], "private": True})
                elif ended.get(main):
                    return ended[main]
                elif ended.get(spare) and (main in ended or _text(ended[spare][2].get("say"))):
                    return ended[spare]
            return None
        finally:
            for drop in drops:
                drop()
            for task in running:
                task.cancel()

    async def _answer(self, session: Session, turn: int, frame: Optional[Frame]) -> None:
        await self._reply(session, turn, frame)
        held, session.held = session.held, False
        now = session.last_frame
        if held and now and not session.heard and not session.ended and _new_screen(session, now):
            session.answer = None  # This one is done, and it is what would be stopped.
            self._start(session, SILENT_TURN, now)  # The screen that had to wait would otherwise go unseen.

    async def _reply(self, session: Session, turn: int, frame: Optional[Frame]) -> None:
        started = time.perf_counter()
        early: dict = {}
        got = await self._ask(session, turn, started, early)
        session.restarts = 0
        if got is None:
            if session.open_words:
                session.publish({"type": "say", "turn": turn, "text_ru": FAILED_LINE, "paste_text": None, "timings": {"model_ms": _ms(started)}})
            else:
                session.publish({"type": "quiet", "turn": turn})
            return
        model, reply, answer = got
        ms = _ms(started)
        private = _private(session, frame, answer)
        if early:
            answer["say"] = early["line"]
            early["turn"]["text"] = json.dumps(answer, ensure_ascii=False)
        else:
            session.chat.append({"role": "assistant", "text": json.dumps(answer, ensure_ascii=False)})
        session.last_answer = answer
        session.timings = {"model_ms": ms}
        lang = _text(answer.get("lang")).lower()
        if re.fullmatch("[a-z]{2}", lang):
            session.app_lang = lang
        if frame and private:  # It is not sent again, and what the model noted is enough for the next turns.
            for kept in session.chat:
                if kept.get("frame") is frame:
                    kept["frame"] = None
        session.note("answer", turn=turn, model=model, ms=ms, first_ms=early.get("ms"), tokens=reply["usage"].get("completion_tokens"), private=private,
                     frame=session.keep_picture(frame, private) if frame else None, **({} if private else {"answer": answer}))
        said, paste = _line(answer.get("say")), _text(answer.get("paste"))
        self._hand_over(session, paste)
        paste_text = paste if session.text_entry == "clipboard" else None
        if not said:  # No sound, so no word from the phone that it has played.
            session.open_words = ""
        if paste_text or (said and not early):
            session.publish({"type": "say", "turn": turn, "text_ru": "" if early else said, "paste_text": paste_text or None, "timings": {"model_ms": ms}})
        elif not said:
            session.publish({"type": "quiet", "turn": turn})
        if session.eyes:
            face = _text(answer.get("face"))
            session.publish({"type": "face", "turn": turn, "face": face if face in FACES and not private else None, "calm": private})
        target = _target(answer.get("show"))
        if target and frame:
            await self._show(session, frame, said or target["label"], target, answer, ms)

    def _hand_over(self, session: Session, paste: str) -> None:
        """The keyboard takes the ready text from here."""
        if paste:
            session.step_sequence += 1
            session.step_id = "s" + str(session.step_sequence)
            session.texts = [paste]

    async def _show(self, session: Session, frame: Frame, text: str, target: dict, answer: dict, model_ms: int) -> None:
        """The voice has gone ahead of the outline. Whatever keeps it away, a new turn included, the model hears on its next turn."""
        started = time.perf_counter()
        unshown = {"role": "user", "text": UNSHOWN, "frame": None}
        session.chat.append(unshown)

        def written() -> dict:  # The words of a payment screen can arrive while this is at work.
            return {"target": None} if _private(session, frame, answer) else {"target": target, "frame": str(frame.timestamp_ms) + ".jpg"}

        if _gone(session, frame):  # Over a screen they have left it would only send them back.
            session.note("show", outcome="left", **written())
            return
        location = await run_in_threadpool(grounding.locate, frame.jpeg, frame.width, frame.height,
                                           {"action": "tap", "target": target}, _text_on(session, frame))
        box_ms = _ms(started)
        session.grounding = location
        if not location["box"]:
            session.note("show", outcome="not found", box_ms=box_ms, **written())
            return
        image = await run_in_threadpool(hint.render, frame.jpeg, location["box"], text, session.eyes)
        if _gone(session, frame):  # A screen that came while the answer was busy stops nothing, so it can only be seen here.
            session.note("show", outcome="left", box_ms=box_ms, **written())
            return
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
            event["calm"] = _private(session, frame, answer)
        session.chat[:] = [kept for kept in session.chat if kept is not unshown]
        session.shown = True
        session.publish(event)
        session.note("show", outcome="shown", step=session.step_id, box=location["box"],
                     box_source=location["source"], box_ms=box_ms, **written())
