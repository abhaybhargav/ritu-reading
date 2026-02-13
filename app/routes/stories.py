"""Story generation and management API routes."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import async_session as db_session_factory, get_db
from app.models import ReadingLevelState, Story, StoryImage, User
from app.services.image_generator import generate_images_for_story
from app.services.story_generator import generate_story

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# In-memory task tracker for background story generation
# ---------------------------------------------------------------------------

_generation_tasks: dict[str, dict] = {}
# Each entry: {
#   "status": "generating" | "done" | "error",
#   "story_id": int | None,
#   "error": str | None,
#   "level": int,
#   "theme": str,
# }


@router.post("/stories/generate", response_class=HTMLResponse)
async def api_generate_story(
    request: Request,
    theme: str = Form(default=""),
    interests: str = Form(default=""),
    level: int | None = Form(default=None),
    child_id: int | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Kick off story generation in the background.

    Returns immediately with a placeholder card that polls for completion.
    """
    # Determine child user
    if child_id:
        result = await db.execute(select(User).where(User.id == child_id))
        child = result.scalar_one_or_none()
    else:
        result = await db.execute(
            select(User).where(User.role == "child_user").limit(1)
        )
        child = result.scalar_one_or_none()

    if not child:
        return HTMLResponse(
            '<div class="text-red-500 p-4">No child user found.</div>',
            status_code=400,
        )

    # Use the explicitly requested level, or fall back to the child's current level
    if level and 1 <= level <= 6:
        pass  # use the provided level
    else:
        result = await db.execute(
            select(ReadingLevelState).where(ReadingLevelState.user_id == child.id)
        )
        level_state = result.scalar_one_or_none()
        level = level_state.current_level if level_state else 1

    # Create a task ID and launch generation in the background
    task_id = uuid.uuid4().hex[:12]
    _generation_tasks[task_id] = {
        "status": "generating",
        "story_id": None,
        "error": None,
        "level": level,
        "theme": theme or "a new adventure",
    }

    asyncio.create_task(
        _background_generate(
            task_id=task_id,
            child_id=child.id,
            level=level,
            theme=theme or None,
            interests=interests or None,
        )
    )

    # Return a placeholder card that polls for completion
    from main import templates
    return templates.TemplateResponse("partials/story_generating.html", {
        "request": request,
        "task_id": task_id,
        "level": level,
        "theme": theme or "a new adventure",
    })


@router.get("/stories/task/{task_id}", response_class=HTMLResponse)
async def story_task_status(
    request: Request,
    task_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Poll endpoint for story generation status.

    While generating → returns the spinner card (keeps polling).
    When done → returns the real story card (stops polling).
    On error → returns an error message (stops polling).
    """
    task = _generation_tasks.get(task_id)

    if not task:
        return HTMLResponse(
            '<div class="text-red-500 p-4 rounded-xl">Task not found.</div>'
        )

    if task["status"] == "generating":
        # Still working — return spinner (continues polling)
        from main import templates
        return templates.TemplateResponse("partials/story_generating.html", {
            "request": request,
            "task_id": task_id,
            "level": task["level"],
            "theme": task["theme"],
        })

    if task["status"] == "error":
        # Clean up task
        _generation_tasks.pop(task_id, None)
        return HTMLResponse(
            f'<div class="bg-red-50 border border-red-200 rounded-xl p-4 text-red-600 '
            f'text-sm font-semibold">Story generation failed. Please try again.</div>'
        )

    # Done — load the story from DB and return a real card
    story_id = task["story_id"]
    _generation_tasks.pop(task_id, None)  # clean up

    result = await db.execute(
        select(Story)
        .where(Story.id == story_id)
        .options(selectinload(Story.images))
    )
    story = result.scalar_one_or_none()

    if not story:
        return HTMLResponse(
            '<div class="text-red-500 p-4">Story not found.</div>'
        )

    from main import templates
    response = templates.TemplateResponse("partials/story_card.html", {
        "request": request,
        "story": story,
    })
    # Tell HTMX to dispatch a custom event so the toast system picks it up
    response.headers["HX-Trigger"] = json.dumps({
        "showToast": {"message": f"✨ \"{story.title}\" is ready to read!", "type": "success"},
    })
    return response


# ---------------------------------------------------------------------------
# Background generation logic
# ---------------------------------------------------------------------------


async def _background_generate(
    task_id: str,
    child_id: int,
    level: int,
    theme: str | None,
    interests: str | None,
) -> None:
    """Run story text + image generation entirely in the background."""
    try:
        # 1. Generate story text via OpenAI
        story_data = await generate_story(
            level=level,
            theme=theme,
            interests=interests,
        )

        # 2. Save to DB
        async with db_session_factory() as db:
            story = Story(
                user_id=child_id,
                level=level,
                title=story_data["title"],
                text=story_data["text"],
                word_count=story_data["word_count"],
                ai_prompt=story_data["prompt"],
                ai_model_meta=story_data["model_meta"],
                theme=story_data.get("theme", ""),
            )
            db.add(story)
            await db.flush()
            story_id = story.id
            await db.commit()

        # 3. Mark task as done
        if task_id in _generation_tasks:
            _generation_tasks[task_id]["status"] = "done"
            _generation_tasks[task_id]["story_id"] = story_id

        # 4. Kick off image generation (also in background)
        asyncio.create_task(
            _generate_and_save_images(story_id, story_data["title"], story_data["text"])
        )

    except Exception as e:
        logger.exception("Background story generation failed for task %s", task_id)
        if task_id in _generation_tasks:
            _generation_tasks[task_id]["status"] = "error"
            _generation_tasks[task_id]["error"] = str(e)


async def _generate_and_save_images(
    story_id: int, title: str, text: str,
):
    """Background task to generate images and persist them."""
    try:
        image_results = await generate_images_for_story(story_id, title, text)
    except Exception:
        logger.exception("Image generation background task failed for story %s", story_id)
        return

    if not image_results:
        return

    async with db_session_factory() as db:
        for img in image_results:
            si = StoryImage(
                story_id=story_id,
                image_path=img["image_path"],
                prompt=img.get("prompt", ""),
                provider_meta=img.get("provider_meta", ""),
            )
            db.add(si)
        await db.commit()


@router.get("/stories/{story_id}", response_class=HTMLResponse)
async def api_get_story_detail(
    request: Request,
    story_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Return story detail partial (for HTMX)."""
    result = await db.execute(
        select(Story)
        .where(Story.id == story_id)
        .options(selectinload(Story.images))
    )
    story = result.scalar_one_or_none()
    if not story:
        return HTMLResponse('<div class="text-red-500">Story not found</div>', status_code=404)

    from main import templates
    return templates.TemplateResponse("partials/story_detail.html", {
        "request": request,
        "story": story,
    })


@router.get("/level_state", response_class=HTMLResponse)
async def api_level_state(
    request: Request,
    child_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Return the child's current level state as an HTMX partial."""
    if child_id:
        result = await db.execute(
            select(ReadingLevelState).where(ReadingLevelState.user_id == child_id)
        )
    else:
        result = await db.execute(
            select(User).where(User.role == "child_user").limit(1)
        )
        child = result.scalar_one_or_none()
        if child:
            result = await db.execute(
                select(ReadingLevelState).where(ReadingLevelState.user_id == child.id)
            )
        else:
            return HTMLResponse('<div>No child found</div>')

    level_state = result.scalar_one_or_none()
    from main import templates
    return templates.TemplateResponse("partials/level_badge.html", {
        "request": request,
        "level_state": level_state,
    })
