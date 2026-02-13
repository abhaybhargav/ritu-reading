"""Daily digest email – summarises each child's reading activity for the day.

Scheduled to run at 22:00 IST (16:30 UTC) every day.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import async_session
from app.models import ReadingAttempt, ReadingLevelState, User
from app.services.email_service import send_email

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")


# ---------------------------------------------------------------------------
# Data structures for digest
# ---------------------------------------------------------------------------


@dataclass
class ChildDaySummary:
    """Aggregated daily stats for one child."""

    child_name: str
    current_level: int
    stories_read: int
    total_attempts: int
    avg_score: float | None
    best_score: float | None
    total_words_read: int
    time_spent_minutes: float
    had_activity: bool


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


async def _get_child_summaries(today_start: dt.datetime, today_end: dt.datetime) -> list[ChildDaySummary]:
    """Query the DB for each child's activity during the given UTC window."""
    summaries: list[ChildDaySummary] = []

    async with async_session() as db:
        # Fetch all active child users
        children_result = await db.execute(
            select(User)
            .where(User.role == "child_user", User.is_active.is_(True))
            .options(selectinload(User.level_state))
        )
        children = children_result.scalars().all()

        for child in children:
            # Attempts completed today
            attempts_q = await db.execute(
                select(ReadingAttempt)
                .where(
                    ReadingAttempt.user_id == child.id,
                    ReadingAttempt.started_at >= today_start,
                    ReadingAttempt.started_at < today_end,
                    ReadingAttempt.ended_at.is_not(None),
                )
                .options(selectinload(ReadingAttempt.story))
            )
            attempts = attempts_q.scalars().all()

            if not attempts:
                summaries.append(ChildDaySummary(
                    child_name=child.display_name,
                    current_level=child.level_state.current_level if child.level_state else 1,
                    stories_read=0,
                    total_attempts=0,
                    avg_score=None,
                    best_score=None,
                    total_words_read=0,
                    time_spent_minutes=0.0,
                    had_activity=False,
                ))
                continue

            # Compute stats
            scores = [a.score_total for a in attempts if a.score_total is not None]
            story_ids = set(a.story_id for a in attempts)

            total_words = sum(
                a.story.word_count for a in attempts if a.story and a.story.word_count
            )

            time_minutes = 0.0
            for a in attempts:
                if a.ended_at and a.started_at:
                    delta = (a.ended_at - a.started_at).total_seconds() / 60.0
                    time_minutes += delta

            summaries.append(ChildDaySummary(
                child_name=child.display_name,
                current_level=child.level_state.current_level if child.level_state else 1,
                stories_read=len(story_ids),
                total_attempts=len(attempts),
                avg_score=round(sum(scores) / len(scores), 1) if scores else None,
                best_score=round(max(scores), 1) if scores else None,
                total_words_read=total_words,
                time_spent_minutes=round(time_minutes, 1),
                had_activity=True,
            ))

    return summaries


# ---------------------------------------------------------------------------
# HTML email builder
# ---------------------------------------------------------------------------

_NO_ACTIVITY_ALERT = """
<div style="background-color:#FEF2F2;border-left:4px solid #DC2626;padding:16px;margin:16px 0;border-radius:4px;">
  <p style="margin:0;color:#991B1B;font-weight:bold;font-size:16px;">
    &#9888;&#65039; No Activity Today
  </p>
  <p style="margin:8px 0 0;color:#7F1D1D;">
    <strong>{child_name}</strong> did not complete any reading sessions today.
    A little practice every day makes a big difference!
  </p>
</div>
"""

_CHILD_ACTIVITY_BLOCK = """
<div style="background-color:#F0FDF4;border-left:4px solid #16A34A;padding:16px;margin:16px 0;border-radius:4px;">
  <p style="margin:0;font-weight:bold;font-size:16px;color:#166534;">
    &#128214; {child_name} — Level {level}
  </p>
  <table style="margin-top:12px;border-collapse:collapse;width:100%;font-size:14px;">
    <tr><td style="padding:4px 12px 4px 0;color:#374151;">Stories read</td>
        <td style="padding:4px 0;font-weight:bold;color:#111827;">{stories_read}</td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#374151;">Reading attempts</td>
        <td style="padding:4px 0;font-weight:bold;color:#111827;">{total_attempts}</td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#374151;">Average score</td>
        <td style="padding:4px 0;font-weight:bold;color:#111827;">{avg_score}</td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#374151;">Best score</td>
        <td style="padding:4px 0;font-weight:bold;color:#111827;">{best_score}</td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#374151;">Words read</td>
        <td style="padding:4px 0;font-weight:bold;color:#111827;">{total_words_read}</td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#374151;">Time spent</td>
        <td style="padding:4px 0;font-weight:bold;color:#111827;">{time_spent} min</td></tr>
  </table>
</div>
"""


def _build_digest_html(summaries: list[ChildDaySummary], date_str: str) -> str:
    """Build a complete HTML email body for the daily digest."""
    any_inactive = any(not s.had_activity for s in summaries)

    # Header
    header_bg = "#DC2626" if any_inactive else "#2563EB"
    header_text = (
        "Daily Reading Digest — Action Needed"
        if any_inactive
        else "Daily Reading Digest"
    )

    blocks: list[str] = []

    # No-activity children first (alerts)
    for s in summaries:
        if not s.had_activity:
            blocks.append(_NO_ACTIVITY_ALERT.format(child_name=s.child_name))

    # Active children
    for s in summaries:
        if s.had_activity:
            blocks.append(_CHILD_ACTIVITY_BLOCK.format(
                child_name=s.child_name,
                level=s.current_level,
                stories_read=s.stories_read,
                total_attempts=s.total_attempts,
                avg_score=f"{s.avg_score}/100" if s.avg_score is not None else "—",
                best_score=f"{s.best_score}/100" if s.best_score is not None else "—",
                total_words_read=s.total_words_read,
                time_spent=s.time_spent_minutes,
            ))

    children_html = "\n".join(blocks) if blocks else (
        '<p style="color:#6B7280;text-align:center;padding:20px;">No children registered yet.</p>'
    )

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background-color:#F3F4F6;font-family:system-ui,-apple-system,sans-serif;">
  <div style="max-width:600px;margin:24px auto;background:#FFFFFF;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1);">
    <!-- Header -->
    <div style="background-color:{header_bg};padding:24px;text-align:center;">
      <h1 style="margin:0;color:#FFFFFF;font-size:22px;">{header_text}</h1>
      <p style="margin:6px 0 0;color:rgba(255,255,255,.85);font-size:14px;">{date_str}</p>
    </div>

    <!-- Body -->
    <div style="padding:24px;">
      {children_html}
    </div>

    <!-- Footer -->
    <div style="background-color:#F9FAFB;padding:16px;text-align:center;font-size:12px;color:#9CA3AF;">
      Sent by Ritu's ReadAlong Tutor &bull; Daily digest at 10:00 PM IST
    </div>
  </div>
</body>
</html>
"""


def _build_digest_text(summaries: list[ChildDaySummary], date_str: str) -> str:
    """Build a plain-text fallback for the daily digest."""
    lines = [f"Daily Reading Digest — {date_str}", "=" * 40, ""]

    for s in summaries:
        if not s.had_activity:
            lines.append(f"⚠️  NO ACTIVITY: {s.child_name} did not read today!")
            lines.append("")

    for s in summaries:
        if s.had_activity:
            lines.append(f"📖 {s.child_name} (Level {s.current_level})")
            lines.append(f"   Stories read: {s.stories_read}")
            lines.append(f"   Attempts: {s.total_attempts}")
            avg = f"{s.avg_score}/100" if s.avg_score is not None else "—"
            best = f"{s.best_score}/100" if s.best_score is not None else "—"
            lines.append(f"   Avg score: {avg}  |  Best: {best}")
            lines.append(f"   Words read: {s.total_words_read}")
            lines.append(f"   Time spent: {s.time_spent_minutes} min")
            lines.append("")

    lines.append("—")
    lines.append("Sent by Ritu's ReadAlong Tutor")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry point (called by scheduler)
# ---------------------------------------------------------------------------


async def send_daily_digest() -> None:
    """Query today's activity for all children and send the digest email."""
    now_ist = dt.datetime.now(IST)
    date_str = now_ist.strftime("%A, %B %d, %Y")

    # "Today" = midnight-to-midnight IST, converted to UTC for DB queries
    today_start_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end_ist = today_start_ist + dt.timedelta(days=1)

    # Convert to UTC-naive datetimes (SQLite stores naive UTC)
    today_start_utc = today_start_ist.astimezone(dt.timezone.utc).replace(tzinfo=None)
    today_end_utc = today_end_ist.astimezone(dt.timezone.utc).replace(tzinfo=None)

    log.info("Generating daily digest for %s (UTC window: %s – %s)",
             date_str, today_start_utc, today_end_utc)

    try:
        summaries = await _get_child_summaries(today_start_utc, today_end_utc)
    except Exception:
        log.exception("Failed to query child activity for digest")
        return

    if not summaries:
        log.info("No children found – skipping digest")
        return

    html = _build_digest_html(summaries, date_str)
    text = _build_digest_text(summaries, date_str)

    any_inactive = any(not s.had_activity for s in summaries)
    subject = (
        f"⚠️ ReadAlong Digest ({date_str}) — No activity recorded!"
        if any_inactive
        else f"📖 ReadAlong Digest ({date_str})"
    )

    recipients = settings.digest_recipient_emails
    if not recipients:
        log.warning("No digest recipients configured – skipping")
        return

    try:
        await send_email(
            to_emails=recipients,
            subject=subject,
            html_body=html,
            text_body=text,
            category="daily_digest",
        )
        log.info("Daily digest sent to %s", recipients)
    except Exception:
        log.exception("Failed to send daily digest email")
