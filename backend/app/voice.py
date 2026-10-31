"""Replaceable speech input and output; the HTTP layer only depends on these interfaces."""
from __future__ import annotations

import io
import json
import urllib.request
import wave
from typing import Iterator, Optional, Protocol

from . import config, meta

SPEECH_TIMEOUT = 10
SPEECH_CHUNK = 4800


class SpeechRecognizer(Protocol):
    def transcribe(self, audio: bytes, language_bias: Optional[str] = None) -> str:
        ...


class SpeechSynthesizer(Protocol):
    def stream(self, text: str) -> Iterator[bytes]:
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

    def stream(self, text: str) -> Iterator[bytes]:
        """Connects before it returns, so a refused request can still become an HTTP error."""
        request = urllib.request.Request(
            f"{config.ELEVENLABS_API_BASE}/text-to-speech/{config.ELEVENLABS_VOICE}/stream?output_format=pcm_24000",
            data=json.dumps({
                "text": text, "model_id": config.ELEVENLABS_MODEL,
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            }).encode(),
            headers={"xi-api-key": config.ELEVENLABS_API_KEY, "Content-Type": "application/json"}, method="POST",
        )
        return self._chunks(urllib.request.urlopen(request, timeout=SPEECH_TIMEOUT))

    def _chunks(self, response) -> Iterator[bytes]:
        with response:
            while chunk := response.read1(SPEECH_CHUNK):
                yield chunk
