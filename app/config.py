"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
IMAGES_DIR = DATA_DIR / "images"
TTS_CACHE_DIR = BASE_DIR / "tts_cache"


@dataclass(frozen=True)
class Settings:
    # --- Database ---
    database_url: str = os.getenv(
        "READING_TUTOR_DB_URL", f"sqlite+aiosqlite:///{DATA_DIR / 'readingtutor.db'}"
    )

    # --- OpenAI ---
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")

    # --- OpenAI Realtime API (streaming STT) ---
    openai_realtime_url: str = "wss://api.openai.com/v1/realtime?intent=transcription"
    openai_realtime_model: str = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-transcribe")

    # --- ElevenLabs ---
    elevenlabs_api_key: str = os.getenv("ELEVENLABS_API_KEY", "")
    elevenlabs_voice_id: str = os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")  # "Sarah" default
    elevenlabs_tts_model: str = os.getenv("ELEVENLABS_TTS_MODEL", "eleven_multilingual_v2")

    # --- Ladybird Readers level word-count ranges ---
    level_word_ranges: dict[int, tuple[int, int]] = field(default_factory=lambda: {
        1: (100, 200),
        2: (200, 300),
        3: (300, 600),
        4: (600, 900),
        5: (900, 1500),
        6: (1500, 2000),
    })

    # --- Reading session ---
    stall_timeout_seconds: float = 5.0
    fuzzy_match_threshold: int = 2  # max edit-distance for "close enough"
    lookahead_window: int = 3  # word alignment lookahead

    # --- Scoring weights ---
    accuracy_max: int = 60
    fluency_max: int = 25
    independence_max: int = 15

    # --- Progression ---
    progression_window: int = 10  # last N attempts considered
    promote_threshold: float = 80.0
    demote_threshold: float = 45.0

    # --- Defaults ---
    default_superuser_email: str = "abhaybhargav@gmail.com"


settings = Settings()

# Ensure directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
