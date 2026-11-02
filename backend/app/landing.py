"""The public page of OjO and its beta form. They share the service with the calls and touch nothing of them."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs

from . import config

log = logging.getLogger(__name__)

PATHS = ("/", "/beta")
PAGE = (Path(__file__).parent.parent / "assets" / "landing.html").read_text(encoding="utf-8")
HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'self'; base-uri 'none'; "
                               "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
}
MAX_BODY_BYTES = 1024
BODY_SECONDS = 10  # A body that never ends would stay open past the window of `Limit`, and the next window could add to it.
MAX_EMAIL_LENGTH = 254
MAX_FILE_BYTES = 1048576  # About 20 000 requests: all that a flood of them can cost the disk.
WINDOW_SECONDS = 600
PER_ADDRESS = 5  # In a window, wrong ones too.
PER_WINDOW = 100  # From everybody. It also bounds what `Limit` holds.
EMAIL = re.compile(r"[^@\s]+@[\w-]+(\.[\w-]+)+")


class NoVisitors(logging.Filter):
    """The journal of the service keeps the address of every request, and the form promises to keep the email and the time alone."""

    def filter(self, record: logging.LogRecord) -> bool:
        path = record.args[2] if isinstance(record.args, tuple) and len(record.args) > 2 else ""
        return str(path).split("?")[0] not in PATHS


logging.getLogger("uvicorn.access").addFilter(NoVisitors())


class Limit:
    """How often the form may come in. The addresses stay here for a window and are never written down."""

    def __init__(self) -> None:
        self.seen: deque = deque()

    def allows(self, address: str) -> bool:
        now = time.monotonic()
        while self.seen and now - self.seen[0][0] >= WINDOW_SECONDS:
            self.seen.popleft()
        if len(self.seen) >= PER_WINDOW or sum(who == address for _, who in self.seen) >= PER_ADDRESS:
            return False
        self.seen.append((now, address))
        return True


def page(state: str = "") -> str:
    return PAGE.replace('data-state=""', f'data-state="{state}"', 1)


def read_form(body: bytes) -> Optional[dict]:
    try:
        return parse_qs(body.decode(), keep_blank_values=True, errors="strict", max_num_fields=4)
    except ValueError:  # What is not UTF-8 is one too.
        return None


def is_email(text: str) -> bool:
    return len(text) <= MAX_EMAIL_LENGTH and text.isprintable() and bool(EMAIL.fullmatch(text))


def keep(email: str) -> bool:
    """Their email and the time, and nothing else. False if it could not be kept: the page must not thank them then."""
    line = json.dumps({"email": email, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, ensure_ascii=False) + "\n"
    try:
        file = os.open(config.BETA_FILE, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.fchmod(file, 0o600)
            if os.fstat(file).st_size >= MAX_FILE_BYTES:
                return False
            return os.write(file, line.encode()) == len(line.encode())  # A full disk can take a part of it.
        finally:
            os.close(file)
    except OSError as error:
        log.warning("Beta file: %s", type(error).__name__)
        return False
