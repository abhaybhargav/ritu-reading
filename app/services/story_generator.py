"""Generate leveled stories using the OpenAI chat API."""

from __future__ import annotations

import json
import logging

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


SYSTEM_PROMPT = """\
You are a children's story writer specialising in leveled readers for young children (ages 5-9).
You write engaging, imaginative, and age-appropriate stories.
Rules:
- Stories MUST be child-safe: no violence, scary content, or adult themes.
- Use simple sentence structure at lower levels.
- Return ONLY valid JSON with keys: "title", "text", "theme".
- "text" should be the full story as a single string with paragraph breaks as \\n\\n.
"""


def _build_user_prompt(
    level: int,
    theme: str | None = None,
    interests: str | None = None,
    practice_words: list[str] | None = None,
) -> str:
    word_range = settings.level_word_ranges.get(level, (100, 200))
    parts = [
        f"Write a story for reading level {level}.",
        f"The story MUST be between {word_range[0]} and {word_range[1]} words long.",
    ]
    if theme:
        parts.append(f"Theme: {theme}.")
    if interests:
        parts.append(f"The child is interested in: {interests}.")
    if practice_words:
        words_str = ", ".join(f'"{w}"' for w in practice_words)
        parts.append(
            f"IMPORTANT: The child is practising these words they previously "
            f"found difficult: {words_str}. "
            f"Naturally weave as many of these words as possible into the story "
            f"(ideally each word appears at least once). Do NOT force them — "
            f"the story must still read naturally and be enjoyable."
        )
    parts.append(
        "Use vocabulary and sentence complexity appropriate for this level. "
        "Lower levels should use short sentences and common words."
    )
    return " ".join(parts)


async def generate_story(
    level: int,
    theme: str | None = None,
    interests: str | None = None,
    practice_words: list[str] | None = None,
) -> dict:
    """
    Generate a story and return {"title": ..., "text": ..., "theme": ..., "prompt": ..., "model_meta": ...}.

    If *practice_words* are supplied, the prompt asks the model to
    incorporate those words naturally so the child gets extra exposure.

    Raises on API or parse errors.
    """
    client = _get_client()
    user_prompt = _build_user_prompt(level, theme, interests, practice_words)

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.9,
        max_tokens=2500,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content or "{}"
    data = json.loads(raw)
    word_count = len(data.get("text", "").split())

    return {
        "title": data.get("title", "Untitled Story"),
        "text": data.get("text", ""),
        "theme": data.get("theme", theme or "general"),
        "word_count": word_count,
        "prompt": user_prompt,
        "model_meta": json.dumps({
            "model": response.model,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
            },
        }),
    }
