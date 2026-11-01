"""A file per call, kept outside the repository, so a call can be read afterwards: what was heard, what each model
answered and which screen it saw. A failure here costs the log and never the call."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import config

log = logging.getLogger(__name__)

FOLDER_MODE = 0o700
FILE_MODE = 0o600


def _private(path: str, flags: int) -> int:
    return os.open(path, flags, FILE_MODE)


def _make_private(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(FOLDER_MODE)


class CallLog:
    def __init__(self, folder: Path):
        self.folder = folder

    @classmethod
    def start(cls, session_id: str) -> Optional["CallLog"]:
        """Nothing is kept unless the server was started with CALLS_DIR."""
        if not config.CALLS_DIR:
            return None
        root = Path(config.CALLS_DIR).expanduser().resolve()
        repository = Path(__file__).resolve().parents[2]
        if root == repository or repository in root.parents:
            log.warning("CALLS_DIR is inside the repository, so nothing is kept.")
            return None
        folder = root / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + session_id)
        try:
            _make_private(root)
            _make_private(folder)
            _make_private(folder / "frames")
        except OSError as error:
            log.warning("Call log: %s", type(error).__name__)
            return None
        return cls(folder)

    def write(self, kind: str, fields: dict) -> None:
        line = json.dumps({"at": datetime.now().astimezone().isoformat(timespec="milliseconds"), "kind": kind, **fields},
                          ensure_ascii=False)
        try:
            with open(self.folder / "call.log", "a", encoding="utf-8", opener=_private) as output:
                output.write(line + "\n")
        except OSError as error:
            log.warning("Call log: %s", type(error).__name__)

    def forget(self, timestamp_ms: int) -> None:
        """The picture goes, and the lines that answer it."""
        name = str(timestamp_ms) + ".jpg"
        log_file = self.folder / "call.log"
        try:
            (self.folder / "frames" / name).unlink(missing_ok=True)
            lines = log_file.read_text(encoding="utf-8").splitlines(True)
            log_file.write_text("".join(line for line in lines if json.loads(line).get("frame") != name), encoding="utf-8")
        except (OSError, ValueError) as error:
            log.warning("Call log: %s", type(error).__name__)

    def keep(self, jpeg: bytes, timestamp_ms: int) -> Optional[str]:
        """Returns the name the log knows this picture by."""
        name = str(timestamp_ms) + ".jpg"
        try:
            with open(self.folder / "frames" / name, "xb", opener=_private) as output:
                output.write(jpeg)
        except FileExistsError:
            pass
        except OSError as error:
            log.warning("Call log: %s", type(error).__name__)
            return None
        return name
