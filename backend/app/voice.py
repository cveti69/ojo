"""Replaceable speech input and output; the HTTP layer only depends on these interfaces."""
from __future__ import annotations

import http.client
import io
import json
import wave
from typing import Iterator, Optional, Protocol

from . import config, meta
from .pool import Pool

SPEECH_TIMEOUT = 10
SPEECH_CHUNK = 4800

connections = Pool()


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
        return self._chunks(connection, response)

    def _chunks(self, connection: http.client.HTTPSConnection, response: http.client.HTTPResponse) -> Iterator[bytes]:
        try:
            while chunk := response.read1(SPEECH_CHUNK):
                yield chunk
        except BaseException:  # The phone hung up halfway too: the rest of the line is still on this connection.
            connection.close()
            raise
        connections.finished(connection)
