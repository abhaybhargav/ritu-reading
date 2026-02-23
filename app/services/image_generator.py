"""Generate kid-friendly story illustrations using OpenAI image generation."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

import httpx
from openai import AsyncOpenAI

from app.config import IMAGES_DIR, settings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


def _build_image_prompt(story_title: str, story_excerpt: str, image_index: int) -> str:
    return (
        f"A child-friendly, colorful, storybook-style illustration for a children's story "
        f"titled \"{story_title}\". Scene {image_index + 1}: {story_excerpt[:300]}. "
        f"Style: warm, inviting, watercolor-like, suitable for ages 5-9. "
        f"All characters should be depicted as Indian Hindu children. "
        f"No text or words in the image."
    )


async def generate_images_for_story(
    story_id: int,
    story_title: str,
    story_text: str,
    num_images: int = 3,
) -> list[dict]:
    """
    Generate illustrations for a story. Returns list of dicts:
    [{"image_path": ..., "prompt": ..., "provider_meta": ...}, ...]
    Non-fatal: returns whatever images succeeded.
    """
    paragraphs = [p.strip() for p in story_text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [story_text[:500]]

    # Pick evenly spaced paragraphs for illustrations
    step = max(1, len(paragraphs) // num_images)
    excerpts = [paragraphs[min(i * step, len(paragraphs) - 1)] for i in range(num_images)]

    results = []
    tasks = []
    for idx, excerpt in enumerate(excerpts):
        tasks.append(_generate_single_image(story_id, story_title, excerpt, idx))

    settled = await asyncio.gather(*tasks, return_exceptions=True)
    for item in settled:
        if isinstance(item, Exception):
            logger.error("Image generation failed: %s", item)
        else:
            results.append(item)

    return results


async def _generate_single_image(
    story_id: int,
    story_title: str,
    excerpt: str,
    index: int,
) -> dict:
    client = _get_client()
    prompt = _build_image_prompt(story_title, excerpt, index)

    response = await client.images.generate(
        model="dall-e-3",
        prompt=prompt,
        size="1024x1024",
        quality="standard",
        n=1,
    )

    image_url = response.data[0].url
    if not image_url:
        raise ValueError("No image URL returned from OpenAI")

    # Download and save the image
    filename = f"story_{story_id}_img_{index}_{hashlib.md5(prompt.encode()).hexdigest()[:8]}.png"
    filepath = IMAGES_DIR / filename

    async with httpx.AsyncClient() as http:
        img_resp = await http.get(image_url, timeout=60)
        img_resp.raise_for_status()
        filepath.write_bytes(img_resp.content)

    return {
        "image_path": f"/images/{filename}",
        "prompt": prompt,
        "provider_meta": f'{{"revised_prompt": "{response.data[0].revised_prompt or ""}"}}',
    }
