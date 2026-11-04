"""Replaceable speech input and output; the HTTP layer only depends on these interfaces."""
from __future__ import annotations

import http.client
import io
import json
import logging
import re
import threading
import time
import wave
from dataclasses import dataclass
from typing import Dict, Iterator, Optional, Protocol, Tuple

from . import config, meta, spend
from .pool import Pool

SPEECH_TIMEOUT = 10
SPEECH_CHUNK = 4800
HISTORY_FIRST = 1.0  # A line reaches the history of the account a moment after its sound.
HISTORY_AGAIN = (3.0, 10.0, 30.0)  # The wait after each try that failed. Then the line waits for the end of a call.
HISTORY_LONGEST = 120.0  # A line whose sound never came to an end, as the phone never took it, is deleted all the same.
HISTORY_FLUSH = 5.0  # The server stops: how long it gives the lines that are left,
HISTORY_FLUSH_AGAIN = 0.5  # and how often it tries each of them meanwhile.
LINE_NUMBER = re.compile(r"[A-Za-z0-9_-]{1,64}")

log = logging.getLogger(__name__)
connections = Pool()
history_connections = Pool()  # Not the voice's: a deletion never takes the warm connection the next line needs.


class SpeechRecognizer(Protocol):
    def transcribe(self, audio: bytes, language_bias: Optional[str] = None) -> str:
        ...


class SpeechSynthesizer(Protocol):
    def stream(self, text: str, voice: Optional[str] = None) -> Iterator[bytes]:
        ...


def validate_wav(audio: bytes) -> None:
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            if (source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype()) != (
                1, 2, 16000, "NONE"
            ):
                raise ValueError("Нужен WAV: моно, 16 бит, 16 кГц.")
            expected = source.getnframes() * 2
            if not expected or len(source.readframes(source.getnframes())) != expected:
                raise ValueError("Повреждённый или пустой WAV.")
    except (wave.Error, EOFError, OSError):
        raise ValueError("Не удалось прочитать WAV.") from None


class MetaSpeechRecognizer:
    def transcribe(self, audio: bytes, language_bias: Optional[str] = None) -> str:
        validate_wav(audio)
        return meta.transcribe(audio, language_bias=language_bias)["text"]


class ElevenLabsSynthesizer:
    """Only reads aloud: every word the agent says still comes from the Meta models."""

    def stream(self, text: str, voice: Optional[str] = None) -> Iterator[bytes]:
        """Connects before it returns, so a refused request can still become an HTTP error."""
        chosen = voice if voice in config.VOICES else config.ELEVENLABS_VOICE
        if spend.ledger.stops():  # Even for a line asked for before the day's stop.
            raise OSError("Лимит расходов на сегодня исчерпан.")
        spend.voice(len(text))  # Before it goes: ElevenLabs charges once it has started.
        connection, response = connections.send(
            "POST", f"{config.ELEVENLABS_API_BASE}/text-to-speech/{chosen}/stream?output_format=pcm_24000",
            json.dumps({
                "text": text, "model_id": config.ELEVENLABS_MODEL,
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            }).encode(),
            {"xi-api-key": config.ELEVENLABS_API_KEY, "Content-Type": "application/json"}, SPEECH_TIMEOUT,
        )
        if response.status >= 400:
            connection.close()
            raise OSError("Голос не ответил.")
        spend.voice(spend.counted(response.getheader("character-cost")) - len(text))  # Their own count, when it is larger.
        # The number of the line in the history of the account. Their post on the history API spells it with underscores.
        number = response.getheader("history-item-id") or response.getheader("history_item_id")
        if number and not LINE_NUMBER.fullmatch(number):
            log.warning("Voice history: a line came with a number we cannot use, so it stays in the history.")
            number = None
        if number:
            history.expect(number)
        return self._chunks(connection, response, number)

    def _chunks(self, connection: http.client.HTTPSConnection, response: http.client.HTTPResponse,
                number: Optional[str]) -> Iterator[bytes]:
        try:
            while chunk := response.read1(SPEECH_CHUNK):
                yield chunk
        except BaseException:  # The phone hung up halfway too: the rest of the line is still on this connection.
            connection.close()
            raise
        finally:
            if number:
                history.played(number)  # Whether the phone took all of the sound or hung up halfway.
        connections.finished(connection)


def delete_line(number: str) -> Tuple[int, str]:
    """One try to take a line out of the history of the account. Gives the status, 0 for no answer at all, and the name
    ElevenLabs gives its answer, or the name of the error. Never their message."""
    try:
        connection, response = history_connections.send(
            "DELETE", f"{config.ELEVENLABS_API_BASE}/history/{number}", None,
            {"xi-api-key": config.ELEVENLABS_API_KEY}, SPEECH_TIMEOUT,
        )
        try:
            body = response.read()
        except BaseException:
            connection.close()
            raise
        history_connections.finished(connection)
    except Exception as error:  # Whatever it was, the line is tried again.
        return 0, type(error).__name__
    return response.status, _answer_name(body)


def _answer_name(body: bytes) -> str:
    """`{"status": "ok"}` when the line is gone, `{"detail": {"status": ..., "message": ...}}` when it is refused."""
    try:
        answer = json.loads(body)
        name = answer["status"] if "status" in answer else answer["detail"]["status"]
    except Exception:  # Any body at all, nested past the recursion limit too.
        return ""
    return re.sub(r"[^A-Za-z0-9_]", "", str(name))[:40]


@dataclass
class _Line:
    started: float  # When ElevenLabs gave its number.
    at: Optional[float]  # When it is tried next. None: it waits for the end of a call.
    held: bool = True  # Its sound is still going to the phone.
    tries: int = 0
    last: bool = False  # Its call is over: when its tries run out, it is let go.


class History:
    """ElevenLabs keeps every line it voices in the history of the account, and we keep nothing after the call. So each line is
    deleted once its sound has gone to the phone, in the background: the voice never waits for it. Only the numbers ElevenLabs
    gave this server with its own lines are deleted, never the history as such: the account holds the owner's own work too."""

    def __init__(self) -> None:
        self.lines: Dict[str, _Line] = {}
        self.changed = threading.Condition()
        self.worker: Optional[threading.Thread] = None
        self.stopping = False

    def expect(self, number: str) -> None:
        with self.changed:
            now = time.monotonic()
            self.lines[number] = _Line(now, now + HISTORY_LONGEST)
            self._wake()

    def played(self, number: str) -> None:
        with self.changed:
            line = self.lines.get(number)
            if line and line.held:
                line.held, line.at = False, time.monotonic() + HISTORY_FIRST
                self._wake()

    def sweep(self, until: Optional[float] = None) -> None:
        """A call ended at `until`, or a call's connection broke: each line whose tries have run out is tried once more. The phone
        asks for the voice outside the session, so any line that started by the end of the call may be that call's: when its
        tries run out, it is let go. Lines of a call still going wait for its own end."""
        with self.changed:
            now = time.monotonic()
            for line in self.lines.values():
                if until is not None and line.started <= until:
                    line.last = True
                if line.at is None and not line.held:
                    line.at = now
            self._wake()

    def flush(self, seconds: float) -> None:
        """The server stops, and what is not deleted by then never will be: till then each line left is tried again and again,
        as a line that has just ended may not be in the history yet."""
        deadline = time.monotonic() + seconds
        with self.changed:
            self.stopping = True
            now = time.monotonic()
            for line in self.lines.values():
                if not line.held:
                    line.at = now if line.at is None else min(line.at, now)
            self._wake()
            while self.lines and time.monotonic() < deadline:
                self.changed.wait(deadline - time.monotonic())
            if self.lines:
                log.warning("Voice history: lines not deleted when the server stopped: %d.", len(self.lines))

    def _wake(self) -> None:
        if self.worker is not None:
            self.changed.notify_all()
        elif any(line.at is not None for line in self.lines.values()):
            self.worker = threading.Thread(target=self._work, name="voice-history", daemon=True)
            self.worker.start()

    def _work(self) -> None:
        try:
            while True:
                with self.changed:
                    number = self._next()
                    if number is None:
                        self.worker = None
                        return
                    self.lines[number].held = False  # Due with its sound still going: two minutes on, the phone never took it.
                status, name = delete_line(number)
                with self.changed:
                    self._tried(number, status, name)
                    self.changed.notify_all()
        finally:  # Should a try break the thread, the next line starts another.
            with self.changed:
                if self.worker is threading.current_thread():
                    self.worker = None

    def _next(self) -> Optional[str]:
        """The line due first, once it is due. None when no line waits on the clock."""
        while True:
            waiting = [(line.at, number) for number, line in self.lines.items() if line.at is not None]
            if not waiting:
                return None
            at, number = min(waiting)
            if at <= time.monotonic():
                return number
            self.changed.wait(at - time.monotonic())

    def _tried(self, number: str, status: int, name: str) -> None:
        line = self.lines.get(number)
        if line is None:
            return
        if 200 <= status < 300:
            del self.lines[number]
            return
        if self.stopping:
            line.at = time.monotonic() + HISTORY_FLUSH_AGAIN
            return
        reason = f"{status} {name}".strip() if status else name
        line.tries += 1
        if line.tries <= len(HISTORY_AGAIN):
            line.at = time.monotonic() + HISTORY_AGAIN[line.tries - 1]
        elif line.last:
            del self.lines[number]
            log.warning("Voice history: a line was not deleted, %s.", reason)
        else:
            if line.tries == len(HISTORY_AGAIN) + 1:
                log.warning("Voice history: a line is not deleted yet, %s. It is tried again when the call ends.", reason)
            line.at = None


history = History()
