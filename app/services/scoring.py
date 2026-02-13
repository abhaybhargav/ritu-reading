"""Compute scores for a reading attempt based on completion.

The primary metric is how much of the story the child actually read
through. This avoids penalising fast readers whose STT lags behind,
and keeps the score encouraging and motivating.
"""

from __future__ import annotations

from typing import Any


def compute_score(
    word_events: list[dict],
    total_words: int,
    duration_seconds: float,
    interventions: int,
    skips: int,
) -> dict[str, Any]:
    """
    Compute a 0-100 reading score focused on COMPLETION.

    Breakdown (out of 100):
      - Completion (0-80): % of story words the cursor reached.
      - Effort     (0-20): bonus for finishing, scaled by time spent.

    word_events: list of {"event_type": "correct"|"fuzzy"|"mismatch"|"skip"|...}

    Returns:
      {
        "total": float,
        "completion": float,
        "effort": float,
        "wpm": float,
        "words_reached": int,
        "summary": {"encouragement": str},
      }
    """
    if total_words == 0:
        return _empty_score()

    # --- Words reached ---
    # The highest word_index seen in events tells us how far the child got.
    max_word_index = 0
    for e in word_events:
        wi = e.get("word_index", 0) if isinstance(e, dict) else 0
        if wi > max_word_index:
            max_word_index = wi

    # words_reached = max_word_index + 1 (0-based index → count)
    words_reached = min(max_word_index + 1, total_words) if word_events else 0
    completion_ratio = words_reached / total_words

    # --- Completion (0-80) ---
    completion_score = round(completion_ratio * 80, 1)

    # --- Effort (0-20) ---
    # Full 20 points if the child reached >=90% of the story.
    # Partial credit scaled linearly otherwise.
    # Small bonus if they actually spent time reading (not instant).
    if completion_ratio >= 0.9:
        effort_score = 20.0
    elif completion_ratio >= 0.5:
        effort_score = round(10 + (completion_ratio - 0.5) / 0.4 * 10, 1)
    elif completion_ratio >= 0.1:
        effort_score = round(completion_ratio / 0.5 * 10, 1)
    else:
        effort_score = round(completion_ratio * 20, 1)

    total_score = round(min(completion_score + effort_score, 100), 1)

    # --- WPM ---
    wpm = (words_reached / duration_seconds * 60) if duration_seconds > 0 else 0

    encouragement = _pick_encouragement(total_score, completion_ratio)

    return {
        "total": total_score,
        "completion": completion_score,
        "effort": effort_score,
        # Keep legacy keys so templates/progression still work
        "accuracy": completion_score,
        "fluency": effort_score,
        "independence": 0,
        "wpm": round(wpm, 1),
        "words_reached": words_reached,
        "summary": {
            "encouragement": encouragement,
            "completion_pct": round(completion_ratio * 100, 1),
            "words_reached": words_reached,
            "total_words": total_words,
        },
    }


def _pick_encouragement(score: float, completion_ratio: float) -> str:
    if completion_ratio >= 0.95:
        return "You finished the whole story! You're a reading superstar! 🌟"
    if completion_ratio >= 0.75:
        return "Wow, you read so much! Almost finished! 📚"
    if completion_ratio >= 0.50:
        return "Great effort! You're more than halfway through! 💪"
    if completion_ratio >= 0.25:
        return "Good start! Try reading a little more next time! 🎉"
    return "Nice try! Every page you read helps you grow! Keep going! 📖"


def _empty_score() -> dict:
    return {
        "total": 0,
        "completion": 0,
        "effort": 0,
        "accuracy": 0,
        "fluency": 0,
        "independence": 0,
        "wpm": 0,
        "words_reached": 0,
        "summary": {
            "encouragement": "Let's try reading together! 📖",
            "completion_pct": 0,
            "words_reached": 0,
            "total_words": 0,
        },
    }
