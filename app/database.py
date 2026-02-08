"""Async SQLAlchemy engine and session helpers."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(settings.database_url, echo=False)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:  # type: ignore[misc]
    """FastAPI dependency that yields an async DB session."""
    async with async_session() as session:
        yield session


async def init_db() -> None:
    """Create all tables (idempotent) and run lightweight migrations."""
    async with engine.begin() as conn:
        from app.models import (  # noqa: F401 – import so Base knows about them
            ProblemWordsAgg,
            ReadingAttempt,
            ReadingLevelState,
            Story,
            StoryImage,
            User,
            WordEvent,
        )
        await conn.run_sync(Base.metadata.create_all)

        # ---- Lightweight column migrations ----
        sa_text = __import__("sqlalchemy").text

        # Add pin_hash column to users table if it doesn't exist (for upgrades)
        try:
            await conn.execute(sa_text("ALTER TABLE users ADD COLUMN pin_hash VARCHAR(64)"))
        except Exception:
            pass  # column already exists

        # Add total_lookups column to problem_words_agg if it doesn't exist
        try:
            await conn.execute(
                sa_text("ALTER TABLE problem_words_agg ADD COLUMN total_lookups INTEGER DEFAULT 0")
            )
        except Exception:
            pass  # column already exists
