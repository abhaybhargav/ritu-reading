"""Adaptive leveling / progression engine."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ReadingAttempt, ReadingLevelState

logger = logging.getLogger(__name__)


async def evaluate_progression(
    db: AsyncSession,
    user_id: int,
) -> dict[str, Any]:
    """
    Look at the last N attempts and decide whether to promote, hold, or demote.

    Returns:
      {"action": "promote"|"hold"|"demote", "new_level": int, "reason": str}
    """
    # Get current level
    result = await db.execute(
        select(ReadingLevelState).where(ReadingLevelState.user_id == user_id)
    )
    level_state = result.scalar_one_or_none()
    if level_state is None:
        return {"action": "hold", "new_level": 1, "reason": "No level state found"}

    current_level = level_state.current_level

    # Get recent attempts
    result = await db.execute(
        select(ReadingAttempt)
        .where(ReadingAttempt.user_id == user_id)
        .where(ReadingAttempt.score_total.isnot(None))
        .order_by(ReadingAttempt.started_at.desc())
        .limit(settings.progression_window)
    )
    attempts = result.scalars().all()

    if len(attempts) < 3:
        return {
            "action": "hold",
            "new_level": current_level,
            "reason": f"Only {len(attempts)} scored attempts; need at least 3",
        }

    # Weighted average (newer = heavier)
    total_weight = 0
    weighted_sum = 0
    for i, attempt in enumerate(attempts):
        weight = len(attempts) - i  # newest gets highest weight
        weighted_sum += (attempt.score_total or 0) * weight
        total_weight += weight

    avg_score = weighted_sum / total_weight if total_weight else 0

    # Accuracy trend
    accuracy_scores = [a.score_accuracy or 0 for a in attempts]
    avg_accuracy = sum(accuracy_scores) / len(accuracy_scores) if accuracy_scores else 0

    # Decision
    max_level = max(settings.level_word_ranges.keys())
    if avg_score >= settings.promote_threshold and current_level < max_level:
        new_level = current_level + 1
        reason = (
            f"Weighted avg score {avg_score:.1f} >= {settings.promote_threshold} "
            f"(accuracy {avg_accuracy:.1f}%) over last {len(attempts)} attempts"
        )
        action = "promote"
    elif avg_score < settings.demote_threshold and current_level > 1:
        new_level = current_level - 1
        reason = (
            f"Weighted avg score {avg_score:.1f} < {settings.demote_threshold} "
            f"over last {len(attempts)} attempts"
        )
        action = "demote"
    else:
        new_level = current_level
        reason = (
            f"Weighted avg score {avg_score:.1f} – holding at level {current_level} "
            f"(accuracy {avg_accuracy:.1f}%)"
        )
        action = "hold"

    # Apply
    level_state.current_level = new_level
    level_state.confidence = min(avg_score / 100, 1.0)
    level_state.last_decision_reason = reason
    await db.commit()

    return {"action": action, "new_level": new_level, "reason": reason}
