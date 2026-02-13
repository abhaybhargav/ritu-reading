"""Mailtrap email sending service."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger(__name__)

MAILTRAP_SEND_URL = "https://send.api.mailtrap.io/api/send"


async def send_email(
    *,
    to_emails: list[str],
    subject: str,
    html_body: str,
    text_body: str | None = None,
    category: str = "daily_digest",
) -> dict[str, Any]:
    """Send an email via the Mailtrap Send API.

    Args:
        to_emails: List of recipient email addresses.
        subject: Email subject line.
        html_body: HTML content of the email.
        text_body: Optional plain-text fallback.
        category: Mailtrap category tag for tracking.

    Returns:
        Mailtrap API response as a dict.

    Raises:
        RuntimeError: If the API token is not configured or the request fails.
    """
    if not settings.mailtrap_api_token:
        log.warning("MAILTRAP_API_TOKEN not set – skipping email send")
        raise RuntimeError("Mailtrap API token is not configured")

    payload: dict[str, Any] = {
        "from": {
            "email": settings.mailtrap_sender_email,
            "name": settings.mailtrap_sender_name,
        },
        "to": [{"email": addr} for addr in to_emails],
        "subject": subject,
        "html": html_body,
        "category": category,
    }
    if text_body:
        payload["text"] = text_body

    headers = {
        "Authorization": f"Bearer {settings.mailtrap_api_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(MAILTRAP_SEND_URL, json=payload, headers=headers)

    if resp.status_code not in (200, 201):
        log.error("Mailtrap API error %s: %s", resp.status_code, resp.text)
        raise RuntimeError(f"Mailtrap API returned {resp.status_code}: {resp.text}")

    log.info("Email sent successfully to %s (subject: %s)", to_emails, subject)
    return resp.json()
