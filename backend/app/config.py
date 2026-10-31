"""Server settings. The product name lives here and nowhere else in the backend."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

APP_NAME = os.environ.get("APP_NAME", "OjO")

META_API_BASE = os.environ.get("META_API_BASE", "https://api.meta.ai/v1").rstrip("/")
META_API_KEY = os.environ.get("META_API_KEY") or os.environ.get("MODEL_API_KEY", "")
META_VISION_MODEL = os.environ.get("META_VISION_MODEL", "")
META_TEXT_MODEL = os.environ.get("META_TEXT_MODEL", "")
META_TALK_MODEL = os.environ.get("META_TALK_MODEL", "muse-spark-1.3")
META_QUICK_MODEL = os.environ.get("META_QUICK_MODEL", "muse-spark-1.2")
META_TRANSCRIBE_MODEL = os.environ.get("META_TRANSCRIBE_MODEL", "muse-voice-transcribe-1.0")
META_SAM_MODEL = os.environ.get("META_SAM_MODEL", "sam-3.1")
META_REASONING = os.environ.get("META_REASONING", "minimal")
META_TALK_REASONING = os.environ.get("META_TALK_REASONING", "low")

ELEVENLABS_API_BASE = os.environ.get("ELEVENLABS_API_BASE", "https://api.elevenlabs.io/v1").rstrip("/")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_v4_turbo")
ELEVENLABS_VOICE = os.environ.get("ELEVENLABS_VOICE", "JBFqnCBsd6RMkjVDRZzb")

GROUNDING_MODE = os.environ.get("GROUNDING_MODE", "direct")
SAVE_FRAMES = os.environ.get("SAVE_FRAMES", "0") == "1"
SHARED_TOKEN = os.environ.get("SHARED_TOKEN", "")

SESSION_TTL_SECONDS = float(os.environ.get("SESSION_TTL_SECONDS", "3600"))
SESSION_CLEANUP_SECONDS = float(os.environ.get("SESSION_CLEANUP_SECONDS", "30"))
MAX_FRAME_BYTES = int(os.environ.get("MAX_FRAME_BYTES", "8388608"))
MAX_FRAME_PIXELS = int(os.environ.get("MAX_FRAME_PIXELS", "20000000"))
MAX_AUDIO_BYTES = int(os.environ.get("MAX_AUDIO_BYTES", "16777216"))
FRAMES_DIR = Path(os.environ.get("FRAMES_DIR", str(Path(tempfile.gettempdir()) / "screen-agent-frames")))
HINT_FONT = Path(__file__).resolve().parents[1] / "assets" / "Inter-Bold.otf"
