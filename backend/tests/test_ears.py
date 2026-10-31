from __future__ import annotations

import asyncio
import json
from collections import deque

from app import config, ears


class FakeSocket:
    def __init__(self):
        self.incoming = deque()
        self.sent = []

    def say(self, *events):
        for event in events:
            self.incoming.append(event if isinstance(event, (bytes, type(None))) else json.dumps(event))

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        while not self.incoming:
            await asyncio.sleep(0.001)
        return self.incoming.popleft()

    def __aiter__(self):
        return self

    async def __anext__(self):
        message = await self.recv()
        if message is None:
            raise StopAsyncIteration
        return message

    async def __aenter__(self):
        return self

    async def __aexit__(self, *details):
        return False


def fake_connection(monkeypatch, *sockets):
    remaining = list(sockets)

    def connect(address, **options):
        assert address == "wss://example.test/v1/asr/realtime"
        if not remaining:
            raise OSError("no more sockets")
        return remaining.pop(0)
    monkeypatch.setattr(config, "META_API_BASE", "https://example.test/v1")
    monkeypatch.setattr(config, "META_API_KEY", "test-key")
    monkeypatch.setattr(config, "META_TRANSCRIBE_MODEL", "test-asr")
    monkeypatch.setattr(ears, "connect", connect)
    monkeypatch.setattr(ears, "RETRY_SECONDS", 0)


async def until(check):
    while not check():
        await asyncio.sleep(0.001)


def run(scenario):
    asyncio.run(asyncio.wait_for(scenario(), 3))


def test_turns_arrive_tidy_and_empty_ones_stay_out(monkeypatch):
    socket = FakeSocket()
    fake_connection(monkeypatch, socket)
    turns = []

    async def scenario():
        hearing = ears.Ears(turns.append)
        socket.say({"sessionId": "live"}, {"type": "speechStart"}, b"\0",
                   {"type": "transcript", "transcript": "Что", "final": False},
                   {"type": "speechComplete", "turnId": 1, "transcript": " Что это за кнопка?.. "},
                   {"type": "speechComplete", "turnId": 2, "transcript": "  "},
                   {"type": "speechComplete", "turnId": 3, "transcript": "Спасибо!!"})
        await until(lambda: len(turns) == 2)
        hearing.close()
    run(scenario)
    assert turns == ["Что это за кнопка?", "Спасибо!"]
    assert json.loads(socket.sent[0]) == {
        "authorization": {"accessToken": "Bearer test-key"}, "audioEncoding": "PCM_16KHZ", "model": "test-asr",
        "mode": "ENDPOINTING", "emitAudioProgress": False,
    }


def test_sound_goes_in_order_and_silence_keeps_the_session_alive(monkeypatch):
    socket = FakeSocket()
    fake_connection(monkeypatch, socket)
    monkeypatch.setattr(ears, "PHONE_GAP", 0.01)
    monkeypatch.setattr(ears, "FRAME_SECONDS", 0.001)

    async def scenario():
        hearing = ears.Ears(lambda said: None)
        hearing.hear(b"first")
        hearing.hear(b"second")
        socket.say({"sessionId": "live"})
        await until(lambda: socket.sent.count(ears.SILENCE) >= 3)
        hearing.close()
    run(scenario)
    assert socket.sent[1:3] == [b"first", b"second"]


def test_sound_waits_for_the_session_and_the_oldest_gives_way(monkeypatch):
    socket = FakeSocket()
    fake_connection(monkeypatch, socket)
    monkeypatch.setattr(ears, "BACKLOG", 2)
    monkeypatch.setattr(ears, "PHONE_GAP", 5)

    async def scenario():
        hearing = ears.Ears(lambda said: None)
        await asyncio.sleep(0)
        for frame in (b"one", b"two", b"three"):
            hearing.hear(frame)
        assert len(socket.sent) == 1
        socket.say({"sessionId": "live"})
        await until(lambda: len(socket.sent) == 3)
        hearing.close()
    run(scenario)
    assert socket.sent[1:] == [b"two", b"three"]


def test_a_lost_session_opens_again(monkeypatch):
    broken, ended, fresh = FakeSocket(), FakeSocket(), FakeSocket()
    fake_connection(monkeypatch, broken, ended, fresh)
    turns = []

    async def scenario():
        hearing = ears.Ears(turns.append)
        broken.say({"sessionId": "one"}, {"type": "error", "message": "gone"})
        ended.say({"sessionId": "two"}, None)
        fresh.say({"sessionId": "three"}, {"type": "speechComplete", "transcript": "Снова слышу."})
        await until(lambda: turns)
        hearing.close()
    run(scenario)
    assert turns == ["Снова слышу."]


def test_a_refused_session_gives_up_quietly(monkeypatch):
    refused = [FakeSocket() for _ in range(ears.RETRIES)]
    for socket in refused:
        socket.say({"type": "error"})
    fake_connection(monkeypatch, *refused)

    async def scenario():
        hearing = ears.Ears(lambda said: None)
        await asyncio.wait_for(hearing.task, 2)
        hearing.hear(b"nobody listens")
        hearing.close()
    run(scenario)
    assert all(len(socket.sent) == 1 for socket in refused)
