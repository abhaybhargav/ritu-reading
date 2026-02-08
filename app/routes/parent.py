"""Parent dashboard and management routes."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import login_redirect, require_role
from app.database import get_db
from app.models import (
    ProblemWordsAgg,
    ReadingAttempt,
    ReadingLevelState,
    Story,
    User,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_parent(request: Request):
    """Check parent auth, return redirect or None."""
    user = require_role(request, "parent_superuser")
    if not user:
        return login_redirect(request)
    return None


# ---- Dashboard ----


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def parent_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    """Parent dashboard page."""
    redirect = _require_parent(request)
    if redirect:
        return redirect

    # Get all children
    result = await db.execute(
        select(User).where(User.role == "child_user")
    )
    children = result.scalars().all()

    # Get the first child for dashboard (can be switched)
    child = children[0] if children else None
    attempts = []
    level_state = None
    problem_words = []
    score_trend = []

    if child:
        # Recent attempts
        result = await db.execute(
            select(ReadingAttempt)
            .where(ReadingAttempt.user_id == child.id)
            .where(ReadingAttempt.score_total.isnot(None))
            .order_by(ReadingAttempt.started_at.desc())
            .limit(10)
        )
        attempts = result.scalars().all()

        # Level state
        result = await db.execute(
            select(ReadingLevelState).where(ReadingLevelState.user_id == child.id)
        )
        level_state = result.scalar_one_or_none()

        # Problem words
        result = await db.execute(
            select(ProblemWordsAgg)
            .where(ProblemWordsAgg.user_id == child.id)
            .order_by(ProblemWordsAgg.total_misses.desc())
            .limit(15)
        )
        problem_words = result.scalars().all()

        # Score trend for chart
        score_trend = [
            {
                "date": a.started_at.strftime("%b %d") if a.started_at else "",
                "score": a.score_total or 0,
                "accuracy": a.score_accuracy or 0,
                "fluency": a.score_fluency or 0,
            }
            for a in reversed(attempts)
        ]

    from main import templates
    return templates.TemplateResponse("parent/dashboard.html", {
        "request": request,
        "children": children,
        "child": child,
        "attempts": attempts,
        "level_state": level_state,
        "problem_words": problem_words,
        "score_trend_json": json.dumps(score_trend),
    })


# ---- HTMX partials ----


@router.get("/partials/attempts", response_class=HTMLResponse)
async def partials_attempts(
    request: Request,
    child_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Return recent attempts list as HTMX partial."""
    if not child_id:
        result = await db.execute(
            select(User).where(User.role == "child_user").limit(1)
        )
        child = result.scalar_one_or_none()
        child_id = child.id if child else None

    if not child_id:
        return HTMLResponse("<p class='text-gray-500'>No child found</p>")

    result = await db.execute(
        select(ReadingAttempt)
        .where(ReadingAttempt.user_id == child_id)
        .where(ReadingAttempt.score_total.isnot(None))
        .order_by(ReadingAttempt.started_at.desc())
        .limit(10)
    )
    attempts = result.scalars().all()

    from main import templates
    return templates.TemplateResponse("partials/attempt_rows.html", {
        "request": request,
        "attempts": attempts,
    })


@router.get("/partials/problem-words", response_class=HTMLResponse)
async def partials_problem_words(
    request: Request,
    child_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Return problem words as HTMX partial."""
    if not child_id:
        result = await db.execute(
            select(User).where(User.role == "child_user").limit(1)
        )
        child = result.scalar_one_or_none()
        child_id = child.id if child else None

    if not child_id:
        return HTMLResponse("<p class='text-gray-500'>No data</p>")

    result = await db.execute(
        select(ProblemWordsAgg)
        .where(ProblemWordsAgg.user_id == child_id)
        .order_by(ProblemWordsAgg.total_misses.desc())
        .limit(15)
    )
    words = result.scalars().all()

    from main import templates
    return templates.TemplateResponse("partials/word_cloud.html", {
        "request": request,
        "problem_words": words,
    })


# ---- Children management ----


@router.get("/children", response_class=HTMLResponse)
async def manage_children(request: Request, db: AsyncSession = Depends(get_db)):
    """Children management page."""
    redirect = _require_parent(request)
    if redirect:
        return redirect

    result = await db.execute(
        select(User)
        .where(User.role == "child_user")
        .options(selectinload(User.level_state))
    )
    children = result.scalars().all()

    from main import templates
    return templates.TemplateResponse("parent/children.html", {
        "request": request,
        "children": children,
    })


@router.post("/children/create", response_class=HTMLResponse)
async def create_child(
    request: Request,
    display_name: str = Form(...),
    email: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
):
    """Create a new child user."""
    # Get parent
    result = await db.execute(
        select(User).where(User.role == "parent_superuser").limit(1)
    )
    parent = result.scalar_one_or_none()

    if not email:
        email = f"{display_name.lower().replace(' ', '')}@readingtutor.local"

    child = User(
        email=email,
        display_name=display_name,
        role="child_user",
        parent_user_id=parent.id if parent else None,
        is_active=True,
    )
    db.add(child)
    await db.flush()

    # Create level state
    level_state = ReadingLevelState(
        user_id=child.id,
        current_level=1,
        confidence=0.5,
        last_decision_reason="Initial level assignment",
    )
    db.add(level_state)
    await db.commit()

    from main import templates
    return templates.TemplateResponse("partials/child_row.html", {
        "request": request,
        "child": child,
    })


# ---- Settings ----


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db: AsyncSession = Depends(get_db)):
    """AI settings page."""
    redirect = _require_parent(request)
    if redirect:
        return redirect

    from app.config import settings as app_settings

    from main import templates
    return templates.TemplateResponse("parent/settings.html", {
        "request": request,
        "settings": app_settings,
    })


@router.post("/children/{child_id}/level", response_class=HTMLResponse)
async def override_level(
    request: Request,
    child_id: int,
    level: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Manually override a child's reading level."""
    result = await db.execute(
        select(ReadingLevelState).where(ReadingLevelState.user_id == child_id)
    )
    level_state = result.scalar_one_or_none()
    if level_state:
        level_state.current_level = level
        level_state.last_decision_reason = "Manual override by parent"
        await db.commit()

    from main import templates
    return templates.TemplateResponse("partials/level_badge.html", {
        "request": request,
        "level_state": level_state,
    })
