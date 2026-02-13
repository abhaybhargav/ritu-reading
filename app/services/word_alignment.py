"""Word alignment between story text and recognised speech.

Tuned for children reading aloud with Indian English accents.
The fuzzy matching is intentionally lenient — it's better to give
a child credit for a close-enough pronunciation than to mark them
wrong because the STT model misheard an accent.

Designed for FAST readers: aggressive lookahead, generous fuzzy
matching in the lookahead window, and high max_advance so the
cursor can keep up with rapid reading.
"""

from __future__ import annotations

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)


def normalise(word: str) -> str:
    """Lower-case, strip punctuation, normalise unicode."""
    word = unicodedata.normalize("NFKD", word).lower()
    word = re.sub(r"[^\w\s]", "", word)
    return word.strip()


def edit_distance(a: str, b: str) -> int:
    """Simple Levenshtein distance."""
    if len(a) < len(b):
        return edit_distance(b, a)
    if len(b) == 0:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost))
        prev = curr
    return prev[len(b)]


# Common phonetic confusions in Indian English STT output.
# Maps normalised recognized → set of normalised expected words it could mean.
_PHONETIC_ALIASES: dict[str, set[str]] = {
    # Indian 't' often sounds like 'th' to models
    "three": {"tree", "three", "free"},
    "tree": {"tree", "three"},
    # Indian 'v'/'w' confusion
    "wery": {"very"},
    "ving": {"wing", "swing"},
    "wing": {"swing", "wing"},
    # Short-word confusions from accent
    "de": {"the", "a"},
    "da": {"the", "a"},
    "d": {"the"},
    "im": {"in", "im"},
    "art": {"are"},
    "matt": {"max"},
    "mac": {"max"},
    "macs": {"max"},
    "ken": {"can"},
    "bali": {"polly"},
    "pali": {"polly"},
    "love": {"loves"},
    "pram": {"from"},
    "batter": {"parrot"},
    "barrett": {"parrot"},
    "lee": {"leo"},
    "menu": {"many"},
    "mean": {"many"},
    "he": {"he", "she", "the"},
    "sure": {"she"},
    "salt": {"talk"},
    "dev": {"the"},
    "animal": {"animals"},
}


def _phonetic_match(recognized: str, expected: str) -> bool:
    """Check if the recognized word is a known phonetic alias for the expected."""
    aliases = _PHONETIC_ALIASES.get(recognized)
    if aliases and expected in aliases:
        return True
    # Also check reverse: if the expected is a key and recognized is in its set
    aliases2 = _PHONETIC_ALIASES.get(expected)
    if aliases2 and recognized in aliases2:
        return True
    return False


def _starts_same(a: str, b: str) -> bool:
    """Check if two words share the same first 2 characters (prefix match)."""
    if len(a) < 2 or len(b) < 2:
        return a[:1] == b[:1] if a and b else False
    return a[:2] == b[:2]


def _fuzzy_ok(recognized: str, expected: str, threshold: int) -> bool:
    """
    Lenient fuzzy matching tuned for accented child speech.

    For a reading tutor we'd rather give credit for a close attempt
    than penalise a child for an accent-related STT error.
    """
    # 1. Phonetic alias table (catches known accent confusions)
    if _phonetic_match(recognized, expected):
        return True

    # 2. Very short words:
    #    1-2 chars: exact only (prevents "a"→"i", "at"→"it" false matches)
    #    3 chars: edit distance 1 but must share first char
    if len(expected) <= 2:
        return recognized == expected
    if len(expected) == 3:
        dist = edit_distance(recognized, expected)
        return dist <= 1 and (recognized[:1] == expected[:1] if recognized else False)

    # 3. Medium words (4-6 chars): allow edit distance up to 2
    if len(expected) <= 6:
        return edit_distance(recognized, expected) <= 2

    # 4. Long words (7+ chars): allow edit distance up to threshold (default 2),
    #    or even 3 if they share the same prefix
    dist = edit_distance(recognized, expected)
    if dist <= threshold:
        return True
    if dist <= threshold + 1 and _starts_same(recognized, expected):
        return True

    return False


def _contains_word(recognized: str, expected: str) -> bool:
    """Check if recognized contains expected or vice-versa (for compound STT output)."""
    if len(expected) >= 3 and expected in recognized:
        return True
    if len(recognized) >= 3 and recognized in expected:
        return True
    return False


def align_transcript_to_story(
    story_words: list[str],
    transcript_text: str,
    current_index: int = 0,
    lookahead: int = 8,
    fuzzy_threshold: int = 2,
    max_advance: int = 30,
) -> list[dict]:
    """
    Align recognised transcript tokens to story words starting from *current_index*.

    Optimised for fast readers:
      - Large lookahead window (8 words) so skipped/mumbled words don't block.
      - Fuzzy matches are allowed in the lookahead window (not just exact).
      - High max_advance (30 words per call) so rapid reading isn't throttled.
      - Substring containment check for compound STT tokens.

    Returns a list of alignment events:
      [{"word_index": int, "expected": str, "recognized": str,
        "match": "correct"|"fuzzy"|"mismatch"|"skip"}, ...]
    """
    transcript_tokens = transcript_text.split()
    events: list[dict] = []
    story_idx = current_index
    trans_idx = 0
    words_advanced = 0

    while (
        trans_idx < len(transcript_tokens)
        and story_idx < len(story_words)
        and words_advanced < max_advance
    ):
        raw_token = transcript_tokens[trans_idx]
        recognized = normalise(raw_token)
        if not recognized:
            trans_idx += 1
            continue

        expected_norm = normalise(story_words[story_idx])

        # --- 1. Exact match at current position ---
        if recognized == expected_norm:
            events.append({
                "word_index": story_idx,
                "expected": story_words[story_idx],
                "recognized": raw_token,
                "match": "correct",
            })
            story_idx += 1
            words_advanced += 1
            trans_idx += 1
            continue

        # --- 2. Look ahead: exact OR fuzzy match within the lookahead window ---
        # If the child skipped a word or two, find where they are now.
        skip_target = -1
        skip_match_type = "correct"
        for offset in range(1, min(lookahead + 1, len(story_words) - story_idx)):
            ahead_norm = normalise(story_words[story_idx + offset])
            if recognized == ahead_norm:
                skip_target = offset
                skip_match_type = "correct"
                break
            if _fuzzy_ok(recognized, ahead_norm, fuzzy_threshold):
                skip_target = offset
                skip_match_type = "fuzzy"
                break
            if _contains_word(recognized, ahead_norm):
                skip_target = offset
                skip_match_type = "fuzzy"
                break

        if skip_target > 0:
            for s in range(skip_target):
                events.append({
                    "word_index": story_idx + s,
                    "expected": story_words[story_idx + s],
                    "recognized": None,
                    "match": "skip",
                })
            events.append({
                "word_index": story_idx + skip_target,
                "expected": story_words[story_idx + skip_target],
                "recognized": raw_token,
                "match": skip_match_type,
            })
            words_advanced += skip_target + 1
            story_idx += skip_target + 1
            trans_idx += 1
            continue

        # --- 3. Fuzzy match at current position ---
        if _fuzzy_ok(recognized, expected_norm, fuzzy_threshold):
            events.append({
                "word_index": story_idx,
                "expected": story_words[story_idx],
                "recognized": raw_token,
                "match": "fuzzy",
            })
            story_idx += 1
            words_advanced += 1
            trans_idx += 1
            continue

        # --- 4. Substring containment at current position ---
        if _contains_word(recognized, expected_norm):
            events.append({
                "word_index": story_idx,
                "expected": story_words[story_idx],
                "recognized": raw_token,
                "match": "fuzzy",
            })
            story_idx += 1
            words_advanced += 1
            trans_idx += 1
            continue

        # --- 5. No match — stay on the same story word ---
        events.append({
            "word_index": story_idx,
            "expected": story_words[story_idx],
            "recognized": raw_token,
            "match": "mismatch",
        })
        trans_idx += 1

    logger.debug(
        "Alignment: %d tokens → %d events, advanced %d words (idx %d→%d)",
        len(transcript_tokens),
        len(events),
        words_advanced,
        current_index,
        story_idx,
    )

    return events
