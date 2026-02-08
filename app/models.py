"""SQLAlchemy ORM models for Ritu's ReadAlong Tutor."""

from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    pin_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # SHA-256 of PIN
    role: Mapped[str] = mapped_column(String(30), nullable=False)  # parent_superuser | child_user
    parent_user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    # relationships
    children: Mapped[list["User"]] = relationship(
        "User", back_populates="parent", foreign_keys=[parent_user_id]
    )
    parent: Mapped[Optional["User"]] = relationship(
        "User", back_populates="children", remote_side=[id]
    )
    level_state: Mapped[Optional["ReadingLevelState"]] = relationship(
        back_populates="user", uselist=False
    )
    stories: Mapped[list["Story"]] = relationship(back_populates="user")
    attempts: Mapped[list["ReadingAttempt"]] = relationship(back_populates="user")


# ---------------------------------------------------------------------------
# Reading level
# ---------------------------------------------------------------------------


class ReadingLevelState(Base):
    __tablename__ = "reading_level_state"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), primary_key=True
    )
    current_level: Mapped[int] = mapped_column(Integer, default=1)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    last_decision_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="level_state")


# ---------------------------------------------------------------------------
# Stories
# ---------------------------------------------------------------------------


class Story(Base):
    __tablename__ = "stories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    ai_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ai_model_meta: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON string
    theme: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    user: Mapped["User"] = relationship(back_populates="stories")
    images: Mapped[list["StoryImage"]] = relationship(
        back_populates="story", cascade="all, delete-orphan"
    )
    attempts: Mapped[list["ReadingAttempt"]] = relationship(back_populates="story")


class StoryImage(Base):
    __tablename__ = "story_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    story_id: Mapped[int] = mapped_column(Integer, ForeignKey("stories.id"))
    image_path: Mapped[str] = mapped_column(String(500), nullable=False)
    prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    provider_meta: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    story: Mapped["Story"] = relationship(back_populates="images")


# ---------------------------------------------------------------------------
# Reading attempts & word events
# ---------------------------------------------------------------------------


class ReadingAttempt(Base):
    __tablename__ = "reading_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    story_id: Mapped[int] = mapped_column(Integer, ForeignKey("stories.id"))
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    ended_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    score_total: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_accuracy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_fluency: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_independence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    interventions_count: Mapped[int] = mapped_column(Integer, default=0)
    skips_count: Mapped[int] = mapped_column(Integer, default=0)
    wpm_estimate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    summary_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="attempts")
    story: Mapped["Story"] = relationship(back_populates="attempts")
    word_events: Mapped[list["WordEvent"]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan"
    )


class WordEvent(Base):
    __tablename__ = "word_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    attempt_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reading_attempts.id")
    )
    story_id: Mapped[int] = mapped_column(Integer, ForeignKey("stories.id"))
    word_index: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_word: Mapped[str] = mapped_column(String(100), nullable=False)
    recognized_word: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    event_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # correct | mismatch | skip | stall | hint
    severity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    timestamp_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    attempt: Mapped["ReadingAttempt"] = relationship(back_populates="word_events")


# ---------------------------------------------------------------------------
# Problem words aggregate
# ---------------------------------------------------------------------------


class ProblemWordsAgg(Base):
    __tablename__ = "problem_words_agg"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    word: Mapped[str] = mapped_column(String(100), nullable=False)
    level_first_seen: Mapped[int] = mapped_column(Integer, nullable=False)
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    total_misses: Mapped[int] = mapped_column(Integer, default=0)
    total_hints: Mapped[int] = mapped_column(Integer, default=0)
    total_lookups: Mapped[int] = mapped_column(Integer, default=0)  # pronunciation popup clicks
    mastery_score: Mapped[float] = mapped_column(Float, default=0.0)
    # mastery_score: 0 = problem, increases +0.34 per correct read, >=1.0 = mastered
