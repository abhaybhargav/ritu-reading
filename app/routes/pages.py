"""HTML page routes (Jinja2 rendered)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_session_user, login_redirect, require_role
from app.database import get_db
from app.models import ReadingAttempt, ReadingLevelState, Story, User

router = APIRouter()


def _templates(request: Request):
    """Helper to get templates from the app state."""
    from main import templates
    return templates


# ---- Child pages ----


@router.get("/", response_class=HTMLResponse)
async def child_home(request: Request, db: AsyncSession = Depends(get_db)):
    """Child home page – shows available stories and reading CTA."""
    # Require child or parent login
    session_user = require_role(request, "child_user", "parent_superuser")
    if not session_user:
        return login_redirect(request)

    # Load the child user from DB
    if session_user["role"] == "child_user":
        result = await db.execute(
            select(User).where(User.id == session_user["user_id"])
        )
    else:
        # Parent can view the child's page too
        result = await db.execute(
            select(User).where(User.role == "child_user").limit(1)
        )
    child = result.scalar_one_or_none()

    stories = []
    level_state = None
    if child:
        result = await db.execute(
            select(Story)
            .where(Story.user_id == child.id)
            .options(selectinload(Story.images))
            .order_by(Story.created_at.desc())
            .limit(50)
        )
        stories = result.scalars().all()

        result = await db.execute(
            select(ReadingLevelState).where(ReadingLevelState.user_id == child.id)
        )
        level_state = result.scalar_one_or_none()

    templates = _templates(request)
    return templates.TemplateResponse("child/home.html", {
        "request": request,
        "child": child,
        "stories": stories,
        "level_state": level_state,
    })


@router.get("/stories/{story_id}", response_class=HTMLResponse)
async def story_reader(request: Request, story_id: int, db: AsyncSession = Depends(get_db)):
    """Read-aloud page for a specific story."""
    session_user = require_role(request, "child_user", "parent_superuser")
    if not session_user:
        return login_redirect(request)

    result = await db.execute(
        select(Story)
        .where(Story.id == story_id)
        .options(selectinload(Story.images))
    )
    story = result.scalar_one_or_none()
    if not story:
        return HTMLResponse("<h1>Story not found</h1>", status_code=404)

    # Get child user
    result = await db.execute(
        select(User).where(User.role == "child_user").limit(1)
    )
    child = result.scalar_one_or_none()

    templates = _templates(request)
    return templates.TemplateResponse("child/reader.html", {
        "request": request,
        "story": story,
        "child": child,
        "words": story.text.split(),
    })


@router.get("/stories/{story_id}/score/{attempt_id}", response_class=HTMLResponse)
async def score_page(
    request: Request,
    story_id: int,
    attempt_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Score summary page after completing a reading."""
    session_user = require_role(request, "child_user", "parent_superuser")
    if not session_user:
        return login_redirect(request)

    result = await db.execute(
        select(ReadingAttempt).where(ReadingAttempt.id == attempt_id)
    )
    attempt = result.scalar_one_or_none()

    result = await db.execute(select(Story).where(Story.id == story_id))
    story = result.scalar_one_or_none()

    if not attempt or not story:
        return HTMLResponse("<h1>Not found</h1>", status_code=404)

    import json
    summary = json.loads(attempt.summary_json) if attempt.summary_json else {}

    templates = _templates(request)
    return templates.TemplateResponse("child/score.html", {
        "request": request,
        "attempt": attempt,
        "story": story,
        "summary": summary,
    })
