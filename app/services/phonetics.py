"""Phonetic breakdown helper for tricky English words.

Returns a child-friendly phonetic guide for words that have
non-obvious pronunciation patterns (silent letters, digraphs, etc.).
"""

from __future__ import annotations

import logging
import re

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pattern-based rules for common tricky patterns.
# If a word matches any rule, we generate a phonetic guide.
# ---------------------------------------------------------------------------

# Words where a trailing 'e' is silent and changes the vowel sound
_SILENT_E_PATTERN = re.compile(
    r"^[a-z]*[bcdfghjklmnpqrstvwxyz][aeiou][bcdfghjklmnpqrstvwxyz]e$", re.I
)

# Words containing 'gh' (often silent or makes 'f' sound)
_GH_PATTERN = re.compile(r"gh", re.I)

# Words containing 'ph' (sounds like 'f')
_PH_PATTERN = re.compile(r"ph", re.I)

# Words with 'kn' at the start (k is silent)
_KN_PATTERN = re.compile(r"^kn", re.I)

# Words with 'wr' at the start (w is silent)
_WR_PATTERN = re.compile(r"^wr", re.I)

# Words with 'tion' / 'sion' (sounds like 'shun' / 'zhun')
_TION_PATTERN = re.compile(r"[ts]ion$", re.I)

# Words with 'ough' (many sounds: 'uff', 'oo', 'oh', 'ow', 'off')
_OUGH_PATTERN = re.compile(r"ough", re.I)

# Words with double letters that might confuse
_DOUBLE_LETTER = re.compile(r"([a-z])\1", re.I)

# Words ending in 'le' (sounds like 'ul')
_LE_ENDING = re.compile(r"[bcdfgkptz]le$", re.I)

# Common words under level 6 that don't need phonetics
_SIMPLE_WORDS = {
    "a", "an", "i", "is", "it", "in", "on", "up", "to", "go", "no",
    "so", "do", "he", "she", "we", "be", "me", "my", "at", "am",
    "the", "and", "but", "not", "you", "was", "are", "his", "her",
    "had", "has", "can", "ran", "big", "red", "see", "saw", "run",
    "fun", "sun", "cat", "dog", "hat", "bat", "sit", "hit", "got",
    "hot", "lot", "let", "get", "set", "put", "cut", "cup", "bus",
    "mud", "bug", "rug", "hug", "dug", "all", "for", "out", "old",
    "new", "now", "how", "too", "two", "did", "say", "said",
}


def _needs_phonetic(word: str) -> bool:
    """Determine whether a word likely has a tricky pronunciation."""
    clean = word.lower().strip(".,!?;:'\"()-")
    if clean in _SIMPLE_WORDS:
        return False
    if len(clean) <= 2:
        return False

    # Check each tricky pattern
    if _SILENT_E_PATTERN.match(clean):
        return True
    if _GH_PATTERN.search(clean):
        return True
    if _PH_PATTERN.search(clean):
        return True
    if _KN_PATTERN.match(clean):
        return True
    if _WR_PATTERN.match(clean):
        return True
    if _TION_PATTERN.search(clean):
        return True
    if _OUGH_PATTERN.search(clean):
        return True
    if _LE_ENDING.search(clean):
        return True
    # For words 5+ chars with double letters
    if len(clean) >= 5 and _DOUBLE_LETTER.search(clean):
        return True

    return False


async def get_phonetic_breakdown(word: str) -> str | None:
    """Return a child-friendly phonetic guide for a word, or None if simple.

    Uses GPT-4o-mini for a quick, accurate breakdown of tricky words.
    For simple words, returns None (no phonetic needed).
    """
    clean = word.strip(".,!?;:'\"()-")

    if not _needs_phonetic(clean):
        return None

    # Use GPT-4o-mini for a quick phonetic breakdown
    if not settings.openai_api_key:
        return _fallback_phonetic(clean)

    try:
        prompt = (
            f'Give a short, child-friendly phonetic pronunciation guide for '
            f'the word "{clean}". Include:\n'
            f'1. How to sound it out in simple syllables (use dashes between syllables)\n'
            f'2. If there are silent letters, say which ones are silent\n'
            f'3. Any special sounds (like "gh" sounding like "f", or silent "e" '
            f'making the vowel say its name)\n\n'
            f'Keep it to 1-2 short lines. Use simple language an 8-year-old would understand.\n'
            f'Example for "night": "Say: nite. The "gh" is silent!"\n'
            f'Example for "make": "Say: mayk. The "e" at the end is silent — '
            f'it makes the "a" say its name!"\n'
            f'Example for "phone": "Say: fone. The "ph" sounds like "f"!"'
        )

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a friendly reading tutor for children. "
                                "Give very short phonetic guides."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 100,
                    "temperature": 0.3,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()

    except Exception as e:
        logger.warning(f"Phonetic API call failed for '{clean}': {e}")
        return _fallback_phonetic(clean)


def _fallback_phonetic(word: str) -> str:
    """Simple rule-based fallback when the API is unavailable."""
    clean = word.lower()
    hints = []

    if _GH_PATTERN.search(clean):
        if clean.endswith("ght"):
            hints.append('The "gh" is silent!')
        elif clean.endswith("ugh"):
            hints.append('The "gh" sounds like "f"!')
        elif clean.endswith("ough"):
            hints.append('"ough" is a tricky sound — listen carefully!')
        else:
            hints.append('The "gh" has a special sound — listen carefully!')

    if _SILENT_E_PATTERN.match(clean):
        hints.append(
            f'The "e" at the end is silent — it makes the vowel say its name!'
        )

    if _PH_PATTERN.search(clean):
        hints.append('"ph" sounds like "f"!')

    if _KN_PATTERN.match(clean):
        hints.append('The "k" is silent — just say the "n"!')

    if _WR_PATTERN.match(clean):
        hints.append('The "w" is silent — just say the "r"!')

    if _TION_PATTERN.search(clean):
        hints.append('"-tion" sounds like "shun"!')

    if hints:
        return " ".join(hints)
    return None
