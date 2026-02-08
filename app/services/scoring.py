"""Compute scores for a reading attempt based on word events."""

from __future__ import annotations

import json
from typing import Any

from app.config import settings


def compute_score(
    word_events: list[dict],
    total_words: int,
    duration_seconds: float,
    interventions: int,
    skips: int,
) -> dict[str, Any]:
    """
    Compute a 0-100 reading score with sub-scores.

    word_events: list of {"event_type": "correct"|"mismatch"|"skip"|"stall"|"hint", ...}

    Returns:
      {
        "total": float,
        "accuracy": float,
        "fluency": float,
        "independence": float,
        "wpm": float,
        "summary": {"wins": [...], "practice_words": [...], "encouragement": str},
      }
    """
    if total_words == 0:
        return _empty_score()

    # --- Accuracy (0-60) ---
    correct = sum(1 for e in word_events if e.get("event_type") in ("correct", "fuzzy"))
    accuracy_ratio = correct / total_words
    accuracy_score = round(accuracy_ratio * settings.accuracy_max, 1)

    # --- Fluency (0-25) ---
    # Based on pace consistency and stalls
    stalls = sum(1 for e in word_events if e.get("event_type") == "stall")
    stall_penalty = min(stalls * 2.5, settings.fluency_max)

    wpm = (total_words / duration_seconds * 60) if duration_seconds > 0 else 0
    # Age-appropriate WPM for level 1-2 is around 40-80
    pace_score = min(wpm / 80.0, 1.0) * settings.fluency_max
    fluency_score = round(max(pace_score - stall_penalty, 0), 1)

    # --- Independence (0-15) ---
    total_helps = interventions + skips
    help_penalty = min(total_helps * 3, settings.independence_max)
    independence_score = round(max(settings.independence_max - help_penalty, 0), 1)

    total_score = round(accuracy_score + fluency_score + independence_score, 1)

    # --- Summary ---
    # Find wins (correctly read tricky words)
    all_correct = [e.get("expected_word", "") for e in word_events
                   if e.get("event_type") == "correct"]
    wins = list(dict.fromkeys(all_correct))[:3]

    # Find practice words (most missed)
    problem = [e.get("expected_word", "") for e in word_events
               if e.get("event_type") in ("mismatch", "stall", "hint")]
    practice_words = list(dict.fromkeys(problem))[:3]

    encouragement = _pick_encouragement(total_score)

    return {
        "total": total_score,
        "accuracy": accuracy_score,
        "fluency": fluency_score,
        "independence": independence_score,
        "wpm": round(wpm, 1),
        "summary": {
            "wins": wins,
            "practice_words": practice_words,
            "encouragement": encouragement,
        },
    }


def _pick_encouragement(score: float) -> str:
    if score >= 85:
        return "Amazing job! You're a reading superstar! 🌟"
    if score >= 70:
        return "Great reading! You're getting better every day! 📚"
    if score >= 50:
        return "Good effort! Keep practising and you'll be even better! 💪"
    return "Nice try! Every time you read, you learn more! Keep going! 🎉"


def _empty_score() -> dict:
    return {
        "total": 0,
        "accuracy": 0,
        "fluency": 0,
        "independence": 0,
        "wpm": 0,
        "summary": {
            "wins": [],
            "practice_words": [],
            "encouragement": "Let's try reading together! 📖",
        },
    }
