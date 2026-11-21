"""Server-side web sessions. The cookie holds a random id; only its SHA-256 is stored."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import Response
from sqlalchemy import delete
from sqlalchemy.orm import Session

from downtube.config import get_config
from downtube.models import OAuthState, WebSession
from downtube.security.crypto import random_token, sha256_hex

COOKIE_NAME = "dt_session"


def create_session(s: Session, user_id: int, user_agent: str | None = None) -> str:
    """Create a session row and return the raw cookie value."""
    cfg = get_config()
    raw = random_token(32)
    now = datetime.now(UTC)
    s.add(
        WebSession(
            id_hash=sha256_hex(raw),
            user_id=user_id,
            csrf_token=random_token(24),
            created_at=now,
            expires_at=now + timedelta(days=cfg.session_days),
            last_seen_at=now,
            user_agent=(user_agent or "")[:400] or None,
        )
    )
    s.flush()
    return raw


def set_cookie(response: Response, raw: str) -> None:
    cfg = get_config()
    response.set_cookie(
        COOKIE_NAME,
        raw,
        max_age=cfg.session_days * 86400,
        httponly=True,
        samesite="lax",
        secure=cfg.secure_cookies,
        path="/",
    )


def clear_cookie(response: Response) -> None:
    cfg = get_config()
    response.delete_cookie(COOKIE_NAME, path="/", secure=cfg.secure_cookies, httponly=True, samesite="lax")


def cleanup_expired(s: Session) -> tuple[int, int]:
    now = datetime.now(UTC)
    a = getattr(s.execute(delete(WebSession).where(WebSession.expires_at < now)), "rowcount", 0)
    b = getattr(s.execute(delete(OAuthState).where(OAuthState.expires_at < now)), "rowcount", 0)
    return a or 0, b or 0
