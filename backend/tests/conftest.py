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

from app import config, grounding, meta
from app.server import create_app

TOKEN = "test-shared-token"
AUTH = {"X-Token": TOKEN}


def answer(**changes):
    result = {
        "screen": "Главный экран", "instruction": "Нажми на поиск", "action": "tap",
        "target": {"label": "Cari", "object": "search bar", "box": [30, 50, 970, 120], "mark_id": None},
        "insert_texts": ["sampo anti ketombe"], "insert_hint_ru": "запрос для поиска",
        "needs_user_confirm": False, "done": False,
    }
    result.update(changes)
    return result


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


def post_frame(client, session_id, image=None, timestamp=123):
    return client.post(
        "/sessions/" + session_id + "/frames", content=jpeg() if image is None else image,
        headers={**AUTH, "Content-Type": "image/jpeg", "X-Frame-Width": "390", "X-Frame-Height": "844",
                 "X-Frame-Ts": str(timestamp)},
    )


def new_session(client, **changes):
    response = client.post("/sessions", headers=AUTH, json={"goal": "Купить шампунь", **changes})
    assert response.status_code == 201
    return response.json()["session_id"]


def idle(backend, session_id):
    async def wait():
        deadline = time.monotonic() + 5
        session = backend.store.sessions[session_id]
        while session.busy:
            if time.monotonic() > deadline:
                raise AssertionError("Analysis did not finish")
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
        self.answers = []
        self.vision_calls = []
        self.efforts = []
        self.text_calls = []
        self.asr_calls = []
        self.asr_reply = {"transcript": "Купить шампунь", "audioDurationMs": 100, "turns": []}
        self.ready_text = "sampo anti ketombe"
        self.hook = None
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def chat(self, model, messages, effort=None, schema=None, timeout=60):
        if model == "test-text":
            self.text_calls.append(messages)
            payload = json.loads(messages[-1]["content"])
            text = "Хорошо, помогу. Включи демонстрацию экрана." if "goal" in payload else "Tolong tunggu di sini."
            if "phone app in" in messages[0]["content"]:
                if self.ready_text is None:
                    raise OSError("no ready text")
                text = self.ready_text
            return {"text": text, "usage": {}, "ms": 1}
        assert model == "test-vision"
        assert schema is not None
        with self.lock:
            self.vision_calls.append(messages)
            self.efforts.append(effort)
            index = len(self.vision_calls)
            response = self.answers.pop(0) if self.answers else answer()
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.hook:
                self.hook(index)
            if isinstance(response, Exception):
                raise response
            return {"text": response if isinstance(response, str) else json.dumps(response, ensure_ascii=False),
                    "usage": {}, "ms": 1}
        finally:
            with self.lock:
                self.active -= 1

    def send(self, method, path, data, content_type, timeout):
        assert method == "POST" and path == "/asr/transcribe"
        self.asr_calls.append((data, content_type))
        return copy.deepcopy(self.asr_reply)


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Real Meta requests are forbidden in tests")
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)


@pytest.fixture
def backend(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SHARED_TOKEN", TOKEN)
    monkeypatch.setattr(config, "META_API_KEY", "test-key")
    monkeypatch.setattr(config, "META_TEXT_MODEL", "test-text")
    monkeypatch.setattr(config, "META_VISION_MODEL", "test-vision")
    monkeypatch.setattr(config, "META_TRANSCRIBE_MODEL", "test-asr")
    monkeypatch.setattr(config, "SAVE_FRAMES", False)
    monkeypatch.setattr(config, "FRAMES_DIR", tmp_path / "frames")
    monkeypatch.setattr(config, "SESSION_TTL_SECONDS", 3600)
    monkeypatch.setattr(config, "SESSION_CLEANUP_SECONDS", 30)
    fake = FakeMeta()
    monkeypatch.setattr(meta, "chat", fake.chat)
    monkeypatch.setattr(meta, "_send", fake.send)

    def locate(image, width, height, model_answer, lines=None, icons=None):
        raw = model_answer["target"]["box"]
        return {"box": [value / 1000 for value in raw] if raw else None, "source": "test", "ms": 1}
    monkeypatch.setattr(grounding, "locate", locate)
    monkeypatch.setattr(grounding, "find_icons", lambda image, width, height: [])
    application = create_app()
    with TestClient(application) as client:
        yield SimpleNamespace(client=client, app=application, store=application.state.sessions, meta=fake)
