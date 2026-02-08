"""Seed the database with default users."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_pin
from app.config import settings
from app.models import ReadingLevelState, User

# Default PINs (hardcoded per user request)
PARENT_PIN = "310313"
CHILD_PIN = "180390"


async def seed_default_users(db: AsyncSession) -> None:
    """Create the superuser parent and a default child if they don't exist.
    Also ensures PINs are set on existing users if missing.
    """

    # --- Parent superuser ---
    result = await db.execute(
        select(User).where(User.email == settings.default_superuser_email)
    )
    parent = result.scalar_one_or_none()
    if parent is None:
        parent = User(
            email=settings.default_superuser_email,
            display_name="Parent",
            role="parent_superuser",
            pin_hash=hash_pin(PARENT_PIN),
            is_active=True,
        )
        db.add(parent)
        await db.flush()
    elif not parent.pin_hash:
        # Back-fill PIN on existing user
        parent.pin_hash = hash_pin(PARENT_PIN)

    # --- Default child ---
    result = await db.execute(
        select(User).where(User.email == "child@readingtutor.local")
    )
    child = result.scalar_one_or_none()
    if child is None:
        child = User(
            email="child@readingtutor.local",
            display_name="Reader",
            role="child_user",
            parent_user_id=parent.id,
            pin_hash=hash_pin(CHILD_PIN),
            is_active=True,
        )
        db.add(child)
        await db.flush()

        # Give child a starting level state
        level_state = ReadingLevelState(
            user_id=child.id,
            current_level=1,
            confidence=0.5,
            last_decision_reason="Initial level assignment",
        )
        db.add(level_state)
    elif not child.pin_hash:
        # Back-fill PIN on existing user
        child.pin_hash = hash_pin(CHILD_PIN)

    await db.commit()
