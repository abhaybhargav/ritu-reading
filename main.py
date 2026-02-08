"""Ritu's ReadAlong Tutor – FastAPI application entry point."""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import IMAGES_DIR
from app.database import async_session, init_db
from app.seed import seed_default_users

# --- Configure logging so app.* loggers are visible alongside uvicorn ---
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:    %(name)s - %(message)s",
    stream=sys.stdout,
    force=True,  # override uvicorn's config
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    # --- startup ---
    await init_db()
    async with async_session() as db:
        await seed_default_users(db)
    yield
    # --- shutdown ---


app = FastAPI(title="Ritu's ReadAlong Tutor", version="0.1.0", lifespan=lifespan)

# Session middleware for PIN-based auth (cookie-signed sessions)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "readalong-coach-dev-secret-change-me"),
    session_cookie="readalong_session",
    max_age=60 * 60 * 24 * 30,  # 30 days
    same_site="lax",
    https_only=False,  # set True in production behind HTTPS
)

# --- Static files & templates ---
STATIC_DIR = Path(__file__).parent / "app" / "static"
TEMPLATES_DIR = Path(__file__).parent / "app" / "templates"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# --- Register routers ---
from app.routes.auth_routes import router as auth_router  # noqa: E402
from app.routes.pages import router as pages_router  # noqa: E402
from app.routes.stories import router as stories_router  # noqa: E402
from app.routes.attempts import router as attempts_router  # noqa: E402
from app.routes.parent import router as parent_router  # noqa: E402

app.include_router(auth_router)
app.include_router(pages_router)
app.include_router(stories_router, prefix="/api")
app.include_router(attempts_router, prefix="/api")
app.include_router(parent_router, prefix="/parent")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
