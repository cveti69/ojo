"""Replaceable speech input; the HTTP layer only depends on this interface."""
from __future__ import annotations

import io
import wave
from typing import Optional, Protocol

from . import meta


class SpeechRecognizer(Protocol):
    def transcribe(self, audio: bytes, language_bias: Optional[str] = None) -> str:
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
