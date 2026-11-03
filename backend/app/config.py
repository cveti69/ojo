"""Server settings. The product name lives here and nowhere else in the backend."""
from __future__ import annotations

import os

APP_NAME = os.environ.get("APP_NAME", "OjO")

META_API_BASE = os.environ.get("META_API_BASE", "https://api.meta.ai/v1").rstrip("/")
META_API_KEY = os.environ.get("META_API_KEY") or os.environ.get("MODEL_API_KEY", "")
META_VISION_MODEL = os.environ.get("META_VISION_MODEL", "")
META_TEXT_MODEL = os.environ.get("META_TEXT_MODEL", "")
META_TALK_MODEL = os.environ.get("META_TALK_MODEL", "muse-wonder-1.0")
META_SPARE_MODEL = os.environ.get("META_SPARE_MODEL", "muse-spark-1.3")
META_TRANSCRIBE_MODEL = os.environ.get("META_TRANSCRIBE_MODEL", "muse-voice-transcribe-1.0")
META_SAM_MODEL = os.environ.get("META_SAM_MODEL", "sam-3.1")
META_REASONING = os.environ.get("META_REASONING", "minimal")
META_TALK_REASONING = os.environ.get("META_TALK_REASONING", "low")
META_FAST_MODEL = os.environ.get("META_FAST_MODEL", "muse-spark-1.3")
META_FAST_REASONING = os.environ.get("META_FAST_REASONING", "minimal")
META_FAST_SPARE_MODEL = os.environ.get("META_FAST_SPARE_MODEL", "muse-wonder-1.0")
META_FAST_SPARE_REASONING = os.environ.get("META_FAST_SPARE_REASONING", "low")

ELEVENLABS_API_BASE = os.environ.get("ELEVENLABS_API_BASE", "https://api.elevenlabs.io/v1").rstrip("/")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_v4_turbo")
ELEVENLABS_VOICE = os.environ.get("ELEVENLABS_VOICE", "JBFqnCBsd6RMkjVDRZzb")
VOICES = dict(pair.split(":", 1) for pair in os.environ.get(
    "ELEVENLABS_VOICES", "JBFqnCBsd6RMkjVDRZzb:George,O4dZDLAkF4Zo2yKOh6R4:Done,xyu8HSCv1JYrhLx4m8UG:Ariana,"
    "gYGXjks6FzTWwNQ6X7hC:Monique,7zoixx2reelPHqsL6WsW:Odom,H1bMkFdFKqJn79KaV9qt:Gordon,snODv2VnG1inLn1Ba0dk:Jeremy").split(","))

SHARED_TOKEN = os.environ.get("SHARED_TOKEN", "")

SESSION_TTL_SECONDS = float(os.environ.get("SESSION_TTL_SECONDS", "3600"))
SESSION_CLEANUP_SECONDS = float(os.environ.get("SESSION_CLEANUP_SECONDS", "30"))
MAX_FRAME_BYTES = int(os.environ.get("MAX_FRAME_BYTES", "8388608"))
MAX_FRAME_PIXELS = int(os.environ.get("MAX_FRAME_PIXELS", "20000000"))
MAX_AUDIO_BYTES = int(os.environ.get("MAX_AUDIO_BYTES", "16777216"))
CALLS_DIR = os.environ.get("CALLS_DIR", "")  # A folder outside the repository for a log of every call. Empty keeps nothing.
BETA_FILE = os.environ.get("BETA_FILE") or (os.path.join(os.path.dirname(CALLS_DIR.rstrip("/")), "beta.jsonl") if CALLS_DIR else "")  # Beside that folder.
LANDING = os.environ.get("LANDING", "") == "on"  # The public page and its form. Off they do not exist, with a token or without one.
DEBUG_PAGE = os.environ.get("DEBUG_PAGE", "") == "on"  # The page of a live call, for a test bench. The token is in every build, so off it does not exist.
