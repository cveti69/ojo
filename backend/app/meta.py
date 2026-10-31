"""Thin client for the Meta Model API, standard library only."""
from __future__ import annotations

import base64
import http.client
import json
import re
import time
import uuid
from typing import Callable, Iterator, Optional

from . import config
from .pool import Pool


# Guards against a runaway answer; one step needs a few hundred tokens.
MAX_COMPLETION_TOKENS = 1500
THINKING_TOKENS = 16000  # Deeper levels think inside this limit, and an answer that runs out of it comes back empty.
ANSWER_TOKENS = 2000  # One spoken turn with the thinking behind it. The longest of 144 measured answers took 1405.

connections = Pool()


class MetaError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"Meta API {status}: {detail}")
        self.status = status


def _request(method: str, path: str, payload: dict | None = None, timeout: float = 60) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    return _send(method, path, data, "application/json", timeout)


def _send(method: str, path: str, data: bytes | None, content_type: str, timeout: float) -> dict:
    if not config.META_API_KEY:
        raise MetaError(503, "Не задан ключ Meta.")
    headers = {"Authorization": f"Bearer {config.META_API_KEY}", "Content-Type": content_type}
    try:
        connection, response = connections.send(method, f"{config.META_API_BASE}{path}", data, headers, timeout)
        try:
            body = response.read()
        except BaseException:
            connection.close()
            raise
        connections.finished(connection)
    except (http.client.HTTPException, OSError):
        raise MetaError(502, "Сервис недоступен.") from None
    if response.status >= 400:
        raise MetaError(response.status, "Запрос отклонён.")
    try:
        reply = json.loads(body)
    except (ValueError, UnicodeError):
        raise MetaError(502, "Некорректный ответ.") from None
    if not isinstance(reply, dict):
        raise MetaError(502, "Некорректный ответ.")
    return reply


def list_models() -> list[str]:
    return [model["id"] for model in _request("GET", "/models")["data"]]


def data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()


def chat(model: str, messages: list, effort: str | None = None, schema: dict | None = None,
         timeout: float = 60) -> dict:
    """One chat completion. Returns {"text", "usage", "ms"}."""
    payload: dict = {"model": model, "messages": messages,
                     "max_completion_tokens": MAX_COMPLETION_TOKENS if effort == "minimal" else THINKING_TOKENS}
    if effort:
        payload["reasoning_effort"] = effort
    if schema:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "answer", "strict": True, "schema": schema},
        }
    started = time.perf_counter()
    reply = _request("POST", "/chat/completions", payload, timeout)
    return {
        "text": reply["choices"][0]["message"]["content"],
        "usage": reply.get("usage", {}),
        "ms": round((time.perf_counter() - started) * 1000),
    }


def _events(response) -> Iterator[dict]:
    """The events of an answer as it comes."""
    for line in iter(response.readline, b""):
        line = line.strip()
        if not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if data == b"[DONE]":
            return
        try:
            event = json.loads(data)
        except ValueError:
            raise MetaError(502, "Некорректный ответ.") from None
        if isinstance(event, dict):
            yield event


def chat_json(model: str, messages: list, effort: str, timeout: float = 60,
              on_text: Optional[Callable[[str], None]] = None) -> dict:
    """One turn of the call. No schema: under a schema, strict or not, Wonder ran on inside a string field in 7 answers of 12
    on real screens, and without one in 0 of 156. Read as it comes: on_text gets the answer so far after each piece, because
    the voice need not wait for the fields only the server uses. That was 0.6 s of 7 on real screens, and no slower overall."""
    if not config.META_API_KEY:
        raise MetaError(503, "Не задан ключ Meta.")
    data = json.dumps({
        "model": model, "messages": messages, "reasoning_effort": effort, "max_completion_tokens": ANSWER_TOKENS,
        "response_format": {"type": "json_object"}, "stream": True, "stream_options": {"include_usage": True},
    }).encode()
    headers = {"Authorization": f"Bearer {config.META_API_KEY}", "Content-Type": "application/json"}
    started = time.perf_counter()
    text, usage = "", {}
    try:
        connection, response = connections.send("POST", f"{config.META_API_BASE}/chat/completions", data, headers, timeout)
        try:
            if response.status < 400:
                for event in _events(response):
                    usage = event.get("usage") or usage
                    for choice in event.get("choices") or []:
                        piece = (choice.get("delta") or {}).get("content")
                        if piece:
                            text += piece
                            if on_text:
                                on_text(text)
            response.read()
        except BaseException:
            connection.close()
            raise
        connections.finished(connection)
    except (http.client.HTTPException, OSError):
        raise MetaError(502, "Сервис недоступен.") from None
    if response.status >= 400:
        raise MetaError(response.status, "Запрос отклонён.")
    return {"text": text, "usage": usage, "ms": round((time.perf_counter() - started) * 1000)}


def text(prompt: str, system: str = "", timeout: float = 60) -> str:
    """Small text tasks use the configured text model."""
    if not config.META_TEXT_MODEL:
        raise MetaError(503, "Не задана текстовая модель.")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    reply = chat(config.META_TEXT_MODEL, messages, effort=config.META_REASONING, timeout=timeout)
    result = reply.get("text")
    if not isinstance(result, str) or not result.strip():
        raise MetaError(502, "Пустой ответ текстовой модели.")
    return result.strip()


def parse_transcription(reply: dict) -> dict:
    """An explicit empty transcript means silence, even when turns are present."""
    containers = [reply]
    for container in containers:
        for key in ("result", "data", "transcription"):
            nested = container.get(key)
            if isinstance(nested, dict):
                containers.append(nested)
    result = {"text": "", "audio_duration_ms": None, "turns": []}
    for container in containers:
        if isinstance(container.get("turns"), list):
            result["turns"] = container["turns"]
        duration = container.get("audioDurationMs")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            result["audio_duration_ms"] = duration
    for container in containers:
        if "transcript" in container:
            transcript = container["transcript"]
            if transcript is None:
                return result
            if not isinstance(transcript, str):
                raise MetaError(502, "Некорректный текст распознавания.")
            result["text"] = transcript.strip()
            return result
    for container in containers:
        if isinstance(container.get("text"), str):
            result["text"] = container["text"].strip()
            return result
    parts = []
    for turn in result["turns"]:
        if isinstance(turn, dict):
            value = turn.get("transcript", turn.get("text", ""))
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    result["text"] = " ".join(parts)
    if not result["turns"]:
        raise MetaError(502, "В ответе нет результата распознавания.")
    return result


def transcribe(wav: bytes, language_bias: str | None = None, keywords: list[str] | None = None,
               timeout: float = 60) -> dict:
    request = {
        "model": config.META_TRANSCRIBE_MODEL,
        "audioEncoding": "WAV",
        "mode": "PUSH_TO_TALK",
    }
    if language_bias:
        request["languageBias"] = language_bias
    if keywords:
        request["keywords"] = keywords
    boundary = "audio-" + uuid.uuid4().hex
    body = (
        ("--" + boundary + '\r\nContent-Disposition: form-data; name="request"\r\n'
         'Content-Type: application/json\r\n\r\n').encode()
        + json.dumps(request, ensure_ascii=False).encode("utf-8")
        + ("\r\n--" + boundary + '\r\nContent-Disposition: form-data; name="audio"; filename="audio.wav"\r\n'
           'Content-Type: audio/wav\r\n\r\n').encode()
        + wav
        + ("\r\n--" + boundary + "--\r\n").encode()
    )
    reply = _send("POST", "/asr/transcribe", body, "multipart/form-data; boundary=" + boundary, timeout)
    return parse_transcription(reply)


def respond(payload: dict, timeout: float = 90) -> dict:
    return _request("POST", "/responses", payload, timeout)


def output_text(reply: dict) -> str:
    parts = []
    for item in reply.get("output", []):
        for piece in item.get("content", []) or []:
            if piece.get("type") == "output_text":
                parts.append(piece.get("text", ""))
    return "".join(parts)


_SAM_BOX = re.compile(r"<\|box;([^|]*)\|>")


def _sam_boxes(text: str) -> list[dict]:
    boxes = []
    for fields in _SAM_BOX.findall(text):
        try:
            values = dict(pair.split("=", 1) for pair in fields.split(";"))
            boxes.append({
                "box": [int(float(values[key])) for key in ("x1", "y1", "x2", "y2")],
                "confidence": float(values["c"]) if "c" in values else None,
            })
        except (KeyError, ValueError):
            raise MetaError(502, "Некорректный ответ.") from None
    return boxes


def sam(jpeg: bytes, prompt: str, timeout: float = 60) -> dict:
    """Segments one image. Returns {"boxes": [{"box", "confidence"}], "ms"}; boxes are in pixels."""
    payload = {
        "model": config.META_SAM_MODEL,
        "metadata": {"include_confidence": "true"},
        "input": [{
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": data_url(jpeg)},
            ],
        }],
    }
    started = time.perf_counter()
    reply = respond(payload, timeout)
    return {
        "boxes": _sam_boxes(output_text(reply)),
        "ms": round((time.perf_counter() - started) * 1000),
    }


def sam_box_prompt(box: list[int], width: int, height: int) -> str:
    x1, y1, x2, y2 = box
    return f"<0f>1<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={width};h={height}|>"


def sam_point_prompt(x: int, y: int, width: int, height: int) -> str:
    return f"<0f>1<|point;x={x};y={y};w={width};h={height}|>"
