"""Simple PIN-based authentication helpers."""

from __future__ import annotations

import hashlib
from typing import Optional

from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


def hash_pin(pin: str) -> str:
    """Hash a PIN with SHA-256."""
    return hashlib.sha256(pin.encode()).hexdigest()


def verify_pin(pin: str, pin_hash: str) -> bool:
    """Verify a PIN against its hash."""
    return hash_pin(pin) == pin_hash


def get_session_user(request: Request) -> dict | None:
    """Get the current user info from the session, or None if not logged in."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return {
        "user_id": user_id,
        "role": request.session.get("role", ""),
        "display_name": request.session.get("display_name", ""),
    }


def require_role(request: Request, *roles: str) -> dict | None:
    """Check if the logged-in user has one of the required roles.

    Returns user info dict if authorised, None otherwise.
    """
    user = get_session_user(request)
    if not user:
        return None
    if user["role"] not in roles:
        return None
    return user


def login_redirect(request: Request) -> RedirectResponse:
    """Redirect to the login page, preserving the intended destination."""
    next_url = request.url.path
    return RedirectResponse(f"/login?next={next_url}", status_code=302)
