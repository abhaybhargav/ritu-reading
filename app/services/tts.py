"""Text-to-speech via OpenAI TTS API with caching."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from openai import AsyncOpenAI

from app.config import TTS_CACHE_DIR, settings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


def _cache_key(voice: str, text: str) -> str:
    return hashlib.sha256(f"{voice}:{text}".encode()).hexdigest()


def get_cached_path(voice: str, text: str) -> Path | None:
    """Return path to cached audio file if it exists."""
    key = _cache_key(voice, text)
    path = TTS_CACHE_DIR / f"{key}.mp3"
    return path if path.exists() else None


async def synthesize_speech(
    text: str,
    voice: str | None = None,
) -> Path:
    """
    Generate TTS audio for the given text via OpenAI TTS.
    Returns the path to the mp3 file.  Uses cache if available.
    """
    voice = voice or settings.openai_tts_voice
    cached = get_cached_path(voice, text)
    if cached:
        return cached

    if not settings.openai_api_key:
        raise RuntimeError("OpenAI API key not configured")

    client = _get_client()
    response = await client.audio.speech.create(
        model=settings.openai_tts_model,
        voice=voice,
        input=text,
        response_format="mp3",
        speed=0.9,  # slightly slower for child pronunciation
    )

    key = _cache_key(voice, text)
    out_path = TTS_CACHE_DIR / f"{key}.mp3"
    out_path.write_bytes(response.content)

    return out_path


def build_coaching_text(expected_word: str) -> str:
    """Build a short coaching phrase for a problem word."""
    return f'The word is "{expected_word}". Can you try saying "{expected_word}"?'
