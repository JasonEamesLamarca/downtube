"""Helpers for endpoints that change the user's YouTube account (unsubscribe, remove a like).

These calls need the optional "manage" permission (``youtube.force-ssl``), which the user grants
separately via ``/api/auth/login?manage=true``. Read-only use of DownTube never needs it.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select

from downtube.auth.oauth import has_manage_scope
from downtube.db import read_session
from downtube.main import ApiError
from downtube.models import AccountStatus, GoogleAccount
from downtube.youtube.client import AuthRevoked, QuotaExceeded, YouTubeClient, YouTubeError

PERMISSION_MESSAGE = (
    "DownTube needs permission to manage your YouTube account for this. "
    "Grant it in Settings → Account & connection, then try again."
)


def _active_account(user_id: int) -> tuple[int, str]:
    with read_session() as s:
        acc = s.scalar(
            select(GoogleAccount).where(GoogleAccount.user_id == user_id, GoogleAccount.status == AccountStatus.active)
        )
        if acc is None:
            raise ApiError(409, "reauth_required", "reconnect your Google account first")
        return acc.id, acc.scopes or ""


def ensure_manage_permission(user_id: int) -> int:
    """Return the account id, or raise 403 ``manage_permission_required``."""
    account_id, scopes = _active_account(user_id)
    if not has_manage_scope(scopes):
        raise ApiError(403, "manage_permission_required", PERMISSION_MESSAGE)
    return account_id


def call_youtube[T](user_id: int, fn: Callable[[YouTubeClient], T]) -> T:
    """Run ``fn`` with a client for the user's account, mapping API failures to API errors."""
    account_id = ensure_manage_permission(user_id)
    try:
        with YouTubeClient(account_id) as yt:
            return fn(yt)
    except AuthRevoked as e:
        raise ApiError(409, "reauth_required", "reconnect your Google account first") from e
    except QuotaExceeded as e:
        raise ApiError(503, "quota_exceeded", "YouTube API quota exhausted; try after the daily reset") from e
    except YouTubeError as e:
        if e.status in (401, 403) and e.reason in (
            "insufficientPermissions",
            "forbidden",
            "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
        ):
            raise ApiError(403, "manage_permission_required", PERMISSION_MESSAGE) from e
        raise ApiError(502, "youtube_error", f"YouTube refused the change: {e.reason or e.status}") from e
