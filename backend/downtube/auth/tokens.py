"""Access-token manager: decrypts stored tokens, refreshes on expiry, flags revoked accounts."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta

import httpx

from downtube.auth.audit import audit
from downtube.config import get_config
from downtube.db import read_session, write_session
from downtube.models import AccountStatus, GoogleAccount
from downtube.security.crypto import decrypt, encrypt

log = logging.getLogger("downtube.auth")

REFRESH_MARGIN = timedelta(seconds=60)


class AuthRevoked(Exception):  # noqa: N818 - name fixed by the contract
    """The account's grant is gone (or the account is not active); the user must reconnect."""


class TokenRefreshError(Exception):
    """Transient failure while refreshing (network / 5xx). The account stays active."""


_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(account_id: int) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(account_id, threading.Lock())


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def mark_reauth_required(account_id: int, detail: str) -> None:
    with write_session() as s:
        acc = s.get(GoogleAccount, account_id)
        if acc is None or acc.status == AccountStatus.reauth_required:
            return
        acc.status = AccountStatus.reauth_required
        acc.status_detail = detail[:500]
        audit(s, "account.reauth_required", acc.user_id, {"reason": detail[:200]})
    log.warning("account %s requires re-authentication: %s", account_id, detail)


def get_access_token(account_id: int, *, force_refresh: bool = False, http: httpx.Client | None = None) -> str:
    """Return a valid access token, refreshing when fewer than 60 s remain.

    Raises AuthRevoked when the account is not active or Google answers ``invalid_grant``.
    """
    with _lock_for(account_id):
        with read_session() as s:
            acc = s.get(GoogleAccount, account_id)
            if acc is None or acc.status != AccountStatus.active:
                raise AuthRevoked("account is not active")
            access_enc, refresh_enc, expires = acc.access_token_enc, acc.refresh_token_enc, _aware(acc.token_expires_at)
        now = datetime.now(UTC)
        if not force_refresh and access_enc and expires and expires - now > REFRESH_MARGIN:
            return decrypt(access_enc)
        if not refresh_enc:
            mark_reauth_required(account_id, "no refresh token stored")
            raise AuthRevoked("no refresh token stored")
        return _refresh(account_id, decrypt(refresh_enc), http)


def _refresh(account_id: int, refresh_token: str, http: httpx.Client | None) -> str:
    cfg = get_config()
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": cfg.google_client_id,
        "client_secret": cfg.google_client_secret,
    }
    own = http is None
    client = http or httpx.Client(timeout=20)
    try:
        try:
            resp = client.post(cfg.google_token_url, data=data)
        except httpx.HTTPError as e:
            raise TokenRefreshError(f"token endpoint unreachable: {type(e).__name__}") from e
    finally:
        if own:
            client.close()
    if resp.status_code != 200:
        try:
            err = resp.json().get("error", "")
        except ValueError:
            err = ""
        if resp.status_code in (400, 401) and err in ("invalid_grant", "invalid_client", "unauthorized_client"):
            mark_reauth_required(account_id, f"refresh failed: {err}")
            raise AuthRevoked(err)
        raise TokenRefreshError(f"token endpoint returned {resp.status_code}")
    body = resp.json()
    access = body["access_token"]
    with write_session() as s:
        acc = s.get(GoogleAccount, account_id)
        if acc is None:
            raise AuthRevoked("account vanished")
        acc.access_token_enc = encrypt(access)
        acc.token_expires_at = datetime.now(UTC) + timedelta(seconds=int(body.get("expires_in", 3600)))
        if body.get("refresh_token"):
            acc.refresh_token_enc = encrypt(body["refresh_token"])
    return access
