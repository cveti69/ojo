from __future__ import annotations

import asyncio
import copy
import io
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, grounding, meta, pool, voice
from app.server import create_app

TOKEN = "test-shared-token"
AUTH = {"X-Token": TOKEN}


def line(say="Это кнопка оплаты.", **fields):
    """One answer of the model."""
    return {"say": say, "notes": "заметка", **fields}


real_locate = grounding.locate


class FakeEars:
    made = []

    def __init__(self, on_turn):
        self.on_turn = on_turn
        self.frames = []
        self.closed = False
        FakeEars.made.append(self)

    def hear(self, frame):
        self.frames.append(frame)

    def close(self):
        self.closed = True


def jpeg(color=(230, 235, 240), size=(390, 844)):
    image = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((12, 44, size[0] - 12, 96), fill=(250, 250, 250), outline=(40, 70, 120), width=3)
    draw.rectangle((24, 180, size[0] - 24, 300), fill=(60, 100, 190))
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=90)
    return output.getvalue()


def wav(rate=16000, channels=1, sample_width=2):
    import wave
    output = io.BytesIO()
    with wave.open(output, "wb") as sound:
        sound.setnchannels(channels)
        sound.setsampwidth(sample_width)
        sound.setframerate(rate)
        sound.writeframes(b"\x00" * (rate * channels * sample_width // 10))
    return output.getvalue()


def post_frame(client, session_id, image=None, timestamp=123, small=False):
    return client.post(
        "/sessions/" + session_id + "/frames", content=jpeg() if image is None else image,
        headers={**AUTH, "Content-Type": "image/jpeg", "X-Frame-Width": "390", "X-Frame-Height": "844",
                 "X-Frame-Ts": str(timestamp), **({"X-Frame-Small": "1"} if small else {})},
    )


def new_session(client, **changes):
    response = client.post("/sessions", headers=AUTH, json=changes)
    assert response.status_code == 201
    return response.json()["session_id"]


def sharing(client, **changes):
    """A call with the screen on."""
    session_id = new_session(client, **changes)
    assert client.post("/sessions/" + session_id + "/broadcast", headers=AUTH, json={"state": "started"}).status_code == 200
    return session_id


def words(client, session_id, text="Что это за кнопка?"):
    assert client.post("/sessions/" + session_id + "/words", headers=AUTH, json={"text": text}).status_code == 200


def say(client, session_id, audio=None):
    return client.post("/sessions/" + session_id + "/say", content=wav() if audio is None else audio,
                       headers={**AUTH, "Content-Type": "audio/wav"})


def look(client, session_id, pictures, first_taken=None):
    names = [str(index if first_taken is None else first_taken + index) + ".jpg" for index in range(len(pictures))]
    parts = [("frame", (name, picture, "image/jpeg")) for name, picture in zip(names, pictures)]
    return client.post("/sessions/" + session_id + "/look", files=parts, headers=AUTH)


def idle(backend, session_id):
    async def wait():
        deadline = time.monotonic() + 5
        session = backend.store.sessions[session_id]
        while session.answer and not session.answer.done():
            if time.monotonic() > deadline:
                raise AssertionError("The answer did not finish")
            await asyncio.sleep(0.005)
    backend.client.portal.call(wait)


def receive_type(websocket, kind):
    for _ in range(15):
        event = websocket.receive_json()
        if event["type"] == kind:
            return event
    raise AssertionError("Missing event " + kind)


class FakeMeta:
    def __init__(self):
        self.replies = []
        self.calls = []
        self.hook = None
        self.text_calls = []
        self.asr_calls = []
        self.asr_reply = {"transcript": "Купить шампунь", "audioDurationMs": 100, "turns": []}
        self.lock = threading.Lock()

    def chat_json(self, model, messages, effort, timeout=60):
        with self.lock:
            self.calls.append(SimpleNamespace(model=model, messages=copy.deepcopy(messages), effort=effort))
            index = len(self.calls)
            response = self.replies.pop(0) if self.replies else line()
        if self.hook:
            self.hook(index)
        if isinstance(response, Exception):
            raise response
        return {"text": response if isinstance(response, str) else json.dumps(response, ensure_ascii=False),
                "usage": {"completion_tokens": 9}, "ms": 1}

    def chat(self, model, messages, effort=None, schema=None, timeout=60):
        assert model == "test-text"
        self.text_calls.append(messages)
        return {"text": "Tolong tunggu di sini.", "usage": {}, "ms": 1}

    def send(self, method, path, data, content_type, timeout):
        assert method == "POST" and path == "/asr/transcribe"
        self.asr_calls.append((data, content_type))
        return copy.deepcopy(self.asr_reply)


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Real Meta requests are forbidden in tests")
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(pool, "connect", forbidden)
    meta.connections.kept.clear()
    voice.connections.kept.clear()


@pytest.fixture
def backend(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SHARED_TOKEN", TOKEN)
    monkeypatch.setattr(config, "META_API_KEY", "test-key")
    monkeypatch.setattr(config, "META_TEXT_MODEL", "test-text")
    monkeypatch.setattr(config, "META_TALK_MODEL", "test-talk")
    monkeypatch.setattr(config, "META_SPARE_MODEL", "test-spare")
    monkeypatch.setattr(config, "META_TRANSCRIBE_MODEL", "test-asr")
    monkeypatch.setattr(config, "CALLS_DIR", "")
    monkeypatch.setattr(config, "SESSION_TTL_SECONDS", 3600)
    monkeypatch.setattr(config, "SESSION_CLEANUP_SECONDS", 30)
    fake = FakeMeta()
    monkeypatch.setattr(meta, "chat", fake.chat)
    monkeypatch.setattr(meta, "chat_json", fake.chat_json)
    monkeypatch.setattr(meta, "_send", fake.send)

    def locate(image, width, height, model_answer, lines=None, icons=None):
        raw = model_answer["target"]["box"]
        return {"box": [value / 1000 for value in raw] if raw else None, "source": "test", "ms": 1}
    monkeypatch.setattr(grounding, "locate", locate)
    monkeypatch.setattr(grounding, "find_icons", lambda image, width, height: [])
    FakeEars.made = []
    application = create_app(ears=FakeEars)
    with TestClient(application) as client:
        yield SimpleNamespace(client=client, app=application, store=application.state.sessions, meta=fake)
