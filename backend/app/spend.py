"""What the server spends in a day on Meta and ElevenLabs. Every answer says what it used, and the prices are the ones they
publish. Past one limit no new call starts, past the other the calls under way end: the app carries the server's token, and
its beta link is public. The day is Bali's, from midnight to midnight WITA."""
from __future__ import annotations

import datetime
import json
import logging
import math
import os
import threading
import time
from typing import Callable, Optional, Tuple

from . import config

log = logging.getLogger(__name__)

WITA = datetime.timezone(datetime.timedelta(hours=8))
MILLION = 1_000_000
# Dollars for a million tokens: input, cached input, output (dev.meta.ai/docs/pricing-rate-limits, 4 November 2026). The cached
# tokens are a part of the input and the thinking a part of the output: so it was in all 2,696 answers of our own runs.
TOKENS = {
    "muse-wonder-1.0": (4.00, 0.20, 20.00),
    "muse-spark-1.3": (1.25, 0.15, 4.25),
    "muse-spark-1.2": (1.25, 0.15, 4.25),
    "muse-spark-1.1": (1.25, 0.15, 4.25),
}
DEAREST = TOKENS["muse-wonder-1.0"]  # For a model the table does not know.
LOST = (20_000, 2_000)  # An answer that broke off before it said what it used: a long turn with a screen, tokens in and out.
AUDIO_SECOND = 0.18 / 3600  # muse-voice-transcribe-1.0, by file or by stream: $0.18 an hour of sound.
IMAGE = 2.50 / 1000  # sam-3.1: $2.50 for a thousand images.
# ElevenLabs, dollars a character through the API, the same on every plan (elevenlabs.io/pricing/api, 4 November 2026).
CHARACTER = {model: 0.04 / 1000 for model in (
    "eleven_v4_turbo", "eleven_v3_conversational", "eleven_flash_v2_5", "eleven_flash_v2", "eleven_turbo_v2_5", "eleven_turbo_v2")}
DEAREST_CHARACTER = 0.08 / 1000  # v4, v3, Multilingual v2, and any model the table does not know.
STEP = 10.0  # The log says each time the day passes another ten dollars.
SAVE_SECONDS = 5.0  # The stream of sound counts ten times a second, so the file is written at most this often.
clock = time.time


def day() -> str:
    return datetime.datetime.fromtimestamp(clock(), WITA).date().isoformat()


class Ledger:
    """The day and its sum, in memory and in a file of two fields. The file holds no words and nothing else."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.day, self.dollars = self._load()
        self.saved_at = -math.inf  # Never yet: the first sum is written at once.
        self.unwritten = False  # The last write failed, and the log has said so once.
        self.when_over: Optional[Callable[[], None]] = None  # Ends the calls under way; the server sets it.

    def add(self, dollars: float) -> None:
        with self.lock:
            self._roll()
            before = self.dollars
            self.dollars += dollars
            said = self._passed(before)
            if said or time.monotonic() - self.saved_at >= SAVE_SECONDS:
                self._save()
            over = before < config.SPEND_STOP <= self.dollars
        if over and self.when_over:
            self.when_over()

    def refuses(self) -> bool:
        """No new call starts."""
        with self.lock:
            self._roll()
            return self.dollars >= min(config.SPEND_LIMIT, config.SPEND_STOP)

    def stops(self) -> bool:
        """Nothing that costs money starts, inside a call or out of one."""
        with self.lock:
            self._roll()
            return self.dollars >= config.SPEND_STOP

    def close(self) -> None:
        with self.lock:
            self._save()

    def _roll(self) -> None:
        today = day()
        if today > self.day:  # Only forward: a clock set back over midnight starts no day again.
            self.day, self.dollars = today, 0.0
            self._save()

    def _passed(self, before: float) -> bool:
        """Says in the log what the day has passed: the limit, the stop, or else another ten dollars."""
        said = False
        for mark, then in ((config.SPEND_LIMIT, "new calls are refused"), (config.SPEND_STOP, "the calls under way end")):
            if before < mark <= self.dollars:
                log.warning("Spend: the day passed $%.2f, %s.", mark, then)
                said = True
        if not said and before // STEP < self.dollars // STEP:
            log.warning("Spend: the day passed $%d.", self.dollars // STEP * STEP)
            said = True
        return said

    def _load(self) -> Tuple[str, float]:
        today = day()
        if not self.path:
            return today, 0.0
        try:
            with open(self.path, encoding="utf-8") as source:
                kept = json.load(source)
            dollars = float(kept["usd"])
            if not math.isfinite(dollars) or dollars < 0:
                raise ValueError
            if kept["day"] == today:
                return today, dollars
        except FileNotFoundError:
            pass
        except Exception as error:  # Whatever is in it, the server starts.
            log.warning("Spend: the file of the day could not be read, %s. The day starts at zero.", type(error).__name__)
        return today, 0.0

    def _save(self) -> None:
        self.saved_at = time.monotonic()
        if not self.path:
            return
        fresh = self.path + ".new"
        try:
            os.makedirs(os.path.dirname(self.path) or ".", mode=0o700, exist_ok=True)
            with open(fresh, "w", encoding="utf-8", opener=lambda path, flags: os.open(path, flags, 0o600)) as output:
                json.dump({"day": self.day, "usd": round(self.dollars, 6)}, output)
            os.chmod(fresh, 0o600)
            os.replace(fresh, self.path)
            self.unwritten = False
        except OSError as error:
            if not self.unwritten:
                log.warning("Spend: the file of the day could not be written, %s.", type(error).__name__)
            self.unwritten = True


def started() -> None:
    """A line in the log at each start of the server: where the day stands."""
    if ledger.path:
        log.warning("Spend: the day %s starts at $%.2f.", ledger.day, ledger.dollars)
    else:
        log.warning("Spend: SPEND_FILE is empty, so each start of the server counts the day from zero.")


def answer(model: str, usage: dict) -> None:
    """One answer of a model. Without its count of tokens it broke off on the way, and Meta may still charge for all of it."""
    price_in, price_cached, price_out = TOKENS.get(model, DEAREST)
    try:
        given, said = max(int(usage["prompt_tokens"]), 0), max(int(usage["completion_tokens"]), 0)
        cached = min(max(int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0), 0), given)
    except (KeyError, TypeError, ValueError, AttributeError):
        (given, said), cached = LOST, 0
    ledger.add(((given - cached) * price_in + cached * price_cached + said * price_out) / MILLION)


def audio(seconds: float) -> None:
    ledger.add(max(seconds, 0.0) * AUDIO_SECOND)


def images(count: int) -> None:
    ledger.add(count * IMAGE)


def voice(characters: float) -> None:
    """ElevenLabs charges by the character."""
    ledger.add(max(characters, 0.0) * CHARACTER.get(config.ELEVENLABS_MODEL, DEAREST_CHARACTER))


def counted(header: Optional[str]) -> float:
    """A count a service sends with its answer, or 0 when there is none to read."""
    try:
        value = float(header or 0)
    except ValueError:
        return 0.0
    return value if math.isfinite(value) and value > 0 else 0.0


ledger = Ledger(config.SPEND_FILE)
