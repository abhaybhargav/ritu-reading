"""Speech-to-text via OpenAI Whisper API."""

from __future__ import annotations

import io
import logging

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

# Minimum audio payload size (bytes) to bother sending.
# Very small blobs are usually silence / recorder artefacts.
MIN_AUDIO_BYTES = 1000

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


async def transcribe_audio(audio_bytes: bytes, language: str = "en") -> dict:
    """
    Send an audio blob to OpenAI Whisper and return the transcript
    with word-level timestamps.

    Returns: {"text": "transcribed words", "words": [...]}
    """
    if not settings.openai_api_key:
        logger.warning("OpenAI API key not set – returning empty transcript")
        return {"text": "", "words": []}

    if len(audio_bytes) < MIN_AUDIO_BYTES:
        logger.debug("Audio too small (%d bytes), skipping STT", len(audio_bytes))
        return {"text": "", "words": []}

    client = _get_client()

    # Wrap raw bytes in a file-like object with a name so the SDK
    # can infer the format from the extension.
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = "audio.webm"

    try:
        transcript = await client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language=language,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )
    except Exception as e:
        logger.error("Whisper STT failed (audio %d bytes): %s", len(audio_bytes), e)
        raise

    text = transcript.text or ""

    # Extract word-level timestamps if available
    words = []
    if hasattr(transcript, "words") and transcript.words:
        words = [
            {
                "word": w.word,
                "start": w.start,
                "end": w.end,
            }
            for w in transcript.words
        ]

    return {"text": text, "words": words}
