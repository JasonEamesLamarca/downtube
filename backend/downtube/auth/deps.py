"""FastAPI dependencies: authentication, CSRF, admin and acknowledgement gates."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Depends, Request
from sqlalchemy import delete
from sqlalchemy.orm import Session

from downtube.auth.sessions import COOKIE_NAME
from downtube.config import get_config
from downtube.db import read_session, write_session
from downtube.main import ApiError
from downtube.models import User, WebSession
from downtube.security.crypto import constant_time_equals, sha256_hex

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def request_db() -> Iterator[Session]:
    """Read-only request session. Writes go through ``db.write_session()`` (RLock held for one call only)."""
    with read_session() as s:
        yield s


@dataclass
class AuthContext:
    user: User
    session: WebSession


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def auth_context(request: Request, db: Session = Depends(request_db)) -> AuthContext:
    """Authenticate the session cookie; enforce CSRF on mutating methods."""
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        raise ApiError(401, "unauthenticated", "authentication required")
    sess = db.get(WebSession, sha256_hex(raw))
    now = datetime.now(UTC)
    if sess is None or _aware(sess.expires_at) < now:
        raise ApiError(401, "unauthenticated", "session missing or expired")
    user = db.get(User, sess.user_id)
    if user is None:
        raise ApiError(401, "unauthenticated", "session missing or expired")
    if not get_config().is_allowed(user.email):  # removed from DOWNTUBE_ALLOWED_EMAILS: revoke immediately
        with write_session() as w:
            w.execute(delete(WebSession).where(WebSession.user_id == user.id))
        raise ApiError(401, "unauthenticated", "account is no longer allowed")
    if request.method in MUTATING:
        sent = request.headers.get("x-csrf-token", "")
        if not sent or not constant_time_equals(sent, sess.csrf_token):
            raise ApiError(403, "csrf_failed", "missing or invalid CSRF token")
    if now - _aware(sess.last_seen_at) > timedelta(minutes=5):  # throttled: avoid a write on every request
        with write_session() as w:
            row = w.get(WebSession, sess.id_hash)
            if row is not None:
                row.last_seen_at = now
    return AuthContext(user=user, session=sess)


def current_user(ctx: AuthContext = Depends(auth_context)) -> User:
    return ctx.user


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise ApiError(403, "forbidden", "administrator access required")
    return user


def ensure_acknowledged(user: User) -> None:
    if user.acknowledged_terms_at is None:
        raise ApiError(
            403,
            "acknowledgement_required",
            "you must acknowledge the rights and responsibility notice before archiving",
        )


def require_acknowledged(user: User = Depends(current_user)) -> User:
    ensure_acknowledged(user)
    return user
