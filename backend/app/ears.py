"""One streaming recognition session for a whole call: Meta hears the person and finds where each turn ends."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from typing import Callable

import websockets

from . import config

log = logging.getLogger(__name__)

FRAME_SECONDS = 0.1
SILENCE = bytes(3200)  # 100 ms of the 16 kHz, 16-bit mono the phone sends. An idle session gets closed.
PHONE_GAP = 0.4  # A gap this short is the network. Past it the phone has stopped sending.
BACKLOG = 50  # Sound kept while the session opens, which takes about two seconds.
ANSWER_SECONDS = 10
RETRY_SECONDS = 1
LONGEST_WAIT = 10
connect = websockets.connect


def tidy(text: str) -> str:
    """Meta can double the mark at the end of a phrase."""
    return re.sub(r"([.!?…])[.!?…]+", r"\1", text.strip())


class Ears:
    def __init__(self, on_turn: Callable[[str], None]):
        self.on_turn = on_turn
        self.frames: asyncio.Queue = asyncio.Queue(maxsize=BACKLOG)
        self.task = asyncio.create_task(self._keep_open())

    def hear(self, frame: bytes) -> None:
        if self.frames.full():
            self.frames.get_nowait()
        self.frames.put_nowait(frame)

    def close(self) -> None:
        self.task.cancel()

    async def _keep_open(self) -> None:
        """A session ends after an hour or an error, and a phrase lost to that is the only cost of a new one.
        Through an outage it keeps trying for as long as the call lasts: the phone speaks through files until it is back."""
        failures = 0
        address = config.META_API_BASE.replace("https://", "wss://", 1) + "/asr/realtime"
        while True:
            try:
                async with connect(address, max_size=None) as socket:
                    await socket.send(json.dumps({
                        "authorization": {"accessToken": "Bearer " + config.META_API_KEY},
                        "audioEncoding": "PCM_16KHZ", "model": config.META_TRANSCRIBE_MODEL,
                        "mode": "ENDPOINTING", "emitAudioProgress": False,
                    }))
                    reply = json.loads(await asyncio.wait_for(socket.recv(), ANSWER_SECONDS))
                    if not reply.get("sessionId"):
                        raise ValueError("Meta refused the session")
                    failures = 0
                    await self._exchange(socket)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                failures += 1
                log.warning("Speech stream: %s", type(error).__name__)
            await asyncio.sleep(min(RETRY_SECONDS * max(failures, 1), LONGEST_WAIT))

    async def _exchange(self, socket) -> None:
        feed = asyncio.create_task(self._feed(socket))
        try:
            async for message in socket:
                if isinstance(message, bytes):
                    continue
                event = json.loads(message)
                if event.get("type") == "speechComplete" and tidy(event.get("transcript", "")):
                    self.on_turn(tidy(event["transcript"]))
                elif event.get("type") == "error":
                    raise ValueError("Meta closed the stream")
        finally:
            feed.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await feed

    async def _feed(self, socket) -> None:
        wait = PHONE_GAP
        while True:
            try:
                frame = await asyncio.wait_for(self.frames.get(), wait)
                wait = PHONE_GAP
            except asyncio.TimeoutError:
                frame = SILENCE
                wait = FRAME_SECONDS
            await socket.send(frame)
