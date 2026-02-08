"""Text-to-speech via ElevenLabs TTS API with caching."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import httpx

from app.config import TTS_CACHE_DIR, settings

logger = logging.getLogger(__name__)

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"


def _cache_key(voice_id: str, text: str) -> str:
    return hashlib.sha256(f"{voice_id}:{text}".encode()).hexdigest()


def get_cached_path(voice_id: str, text: str) -> Path | None:
    """Return path to cached audio file if it exists."""
    key = _cache_key(voice_id, text)
    path = TTS_CACHE_DIR / f"{key}.mp3"
    return path if path.exists() else None


async def synthesize_speech(
    text: str,
    voice_id: str | None = None,
) -> Path:
    """
    Generate TTS audio for the given text.  Returns the path to the mp3 file.
    Uses cache if available.
    """
    voice_id = voice_id or settings.elevenlabs_voice_id
    cached = get_cached_path(voice_id, text)
    if cached:
        return cached

    if not settings.elevenlabs_api_key:
        raise RuntimeError("ElevenLabs API key not configured")

    url = f"{ELEVENLABS_TTS_URL}/{voice_id}"
    headers = {
        "xi-api-key": settings.elevenlabs_api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": settings.elevenlabs_tts_model,
        "voice_settings": {
            "stability": 0.6,
            "similarity_boost": 0.8,
        },
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()

    key = _cache_key(voice_id, text)
    out_path = TTS_CACHE_DIR / f"{key}.mp3"
    out_path.write_bytes(response.content)

    return out_path


def build_coaching_text(expected_word: str) -> str:
    """Build a short coaching phrase for a problem word."""
    return f'The word is "{expected_word}". Can you try saying "{expected_word}"?'
