"""One streaming recognition session for a whole call: Meta hears the person and finds where each turn ends."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from typing import Callable, Optional

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
now = time.monotonic


def tidy(text: str) -> str:
    """Meta can double the mark at the end of a phrase."""
    return re.sub(r"([.!?…])[.!?…]+", r"\1", text.strip())


def _same_words(one: str, other: str) -> bool:
    """A mark or a capital would not change what the model thinks."""
    return re.findall(r"\w+", one.lower()) == re.findall(r"\w+", other.lower())


class Timing:
    """How long before the finished phrase its end and its last draft were known. Only numbers leave this class, and a
    draft goes with its turn. Turns can overlap, and a draft names no turn: it belongs to the one opened last."""

    def __init__(self):
        self.turns: dict = {}
        self.opened = None

    def see(self, event: dict) -> Optional[dict]:
        """Gives the numbers when the turn is finished."""
        kind = event.get("type")
        if kind == "speechStart":
            self.opened = event.get("turnId")
        elif kind == "transcript" and not event.get("final"):
            turn = self.turns.setdefault(self.opened, {})
            if event.get("transcript") != turn.get("draft"):
                turn.update(draft=event.get("transcript"), drafted=now())
        elif kind == "speechEnd":
            self.turns.setdefault(event.get("turnId"), {})["ended"] = now()
        elif kind == "speechComplete":
            turn = self.turns.pop(event.get("turnId"), {})
            done = now()
            return {
                "end_ms": round((done - turn["ended"]) * 1000) if "ended" in turn else None,
                "draft_ms": round((done - turn["drafted"]) * 1000) if "drafted" in turn else None,
                "same": _same_words(turn["draft"] or "", event.get("transcript", "")) if "drafted" in turn else None,
            }
        return None


class Ears:
    def __init__(self, on_turn: Callable[[str], None], on_timing: Callable[..., None] = lambda **numbers: None):
        self.on_turn = on_turn
        self.on_timing = on_timing
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
                log.warning("Speech stream: %s %s", type(error).__name__, str(error)[:120])
            await asyncio.sleep(min(RETRY_SECONDS * max(failures, 1), LONGEST_WAIT))

    async def _exchange(self, socket) -> None:
        feed = asyncio.create_task(self._feed(socket))
        timing = Timing()
        try:
            async for message in socket:
                if isinstance(message, bytes):
                    continue
                event = json.loads(message)
                self._time(timing, event)
                if event.get("type") == "speechComplete" and tidy(event.get("transcript", "")):
                    self.on_turn(tidy(event["transcript"]))
                elif event.get("type") == "error":
                    raise ValueError(f"Meta closed the stream: {event.get('code') or event.get('message')}")
        finally:
            feed.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await feed

    def _time(self, timing: Timing, event: dict) -> None:
        """Only for the log, so nothing here may cost a turn."""
        try:
            numbers = timing.see(event)
            if numbers and tidy(event.get("transcript", "")):
                self.on_timing(**numbers)
        except Exception as error:
            log.warning("Speech timing: %s", type(error).__name__)

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
