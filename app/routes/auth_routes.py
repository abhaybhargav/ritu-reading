"""Login / logout routes with PIN-based authentication."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_session_user, hash_pin, verify_pin
from app.database import get_db
from app.models import User

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Render the PIN login page."""
    # If already logged in, redirect to the appropriate home
    user = get_session_user(request)
    if user:
        if user["role"] == "parent_superuser":
            return RedirectResponse("/parent", status_code=302)
        return RedirectResponse("/", status_code=302)

    next_url = request.query_params.get("next", "/")
    from main import templates
    return templates.TemplateResponse("login.html", {
        "request": request,
        "next_url": next_url,
        "error": None,
    })


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    pin: str = Form(...),
    next_url: str = Form(default="/"),
    db: AsyncSession = Depends(get_db),
):
    """Verify PIN and create a session."""
    pin = pin.strip()

    if not pin:
        from main import templates
        return templates.TemplateResponse("login.html", {
            "request": request,
            "next_url": next_url,
            "error": "Please enter your PIN.",
        })

    pin_hashed = hash_pin(pin)

    # Look up user by PIN hash
    result = await db.execute(
        select(User).where(User.pin_hash == pin_hashed, User.is_active == True)
    )
    user = result.scalar_one_or_none()

    if not user:
        from main import templates
        return templates.TemplateResponse("login.html", {
            "request": request,
            "next_url": next_url,
            "error": "Wrong PIN. Try again!",
        })

    # Set session
    request.session["user_id"] = user.id
    request.session["role"] = user.role
    request.session["display_name"] = user.display_name

    # Redirect based on role if next_url is just /login
    if next_url in ("/login", ""):
        next_url = "/parent" if user.role == "parent_superuser" else "/"

    return RedirectResponse(next_url, status_code=302)


@router.get("/logout")
async def logout(request: Request):
    """Clear the session and redirect to login."""
    request.session.clear()
    return RedirectResponse("/login", status_code=302)
