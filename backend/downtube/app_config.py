"""Runtime configuration that can be set in the app (first-run setup page / Settings).

Google OAuth client credentials and the account allowlist may come from the environment
(``GOOGLE_CLIENT_ID``/``GOOGLE_CLIENT_SECRET``, ``DOWNTUBE_ALLOWED_EMAILS``,
``DOWNTUBE_ADMIN_EMAILS``) — the environment always wins and is shown read-only — or from the
``settings`` table, where the client secret is Fernet-encrypted.

While sign-in is not configured the app is in *setup mode*: :func:`new_setup_code` issues a
one-time code (only its SHA-256 is stored) that is printed in the container log at startup and
must be presented to ``POST /api/setup``.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from downtube.config import get_config
from downtube.db import read_session
from downtube.models import AuditEvent, Setting
from downtube.schemas import GoogleSettingsOut, GoogleSettingsUpdate, SetupRequest, SetupStatus
from downtube.security.crypto import constant_time_equals, decrypt, encrypt, sha256_hex

K_CLIENT_ID = "google_client_id"
K_CLIENT_SECRET = "google_client_secret_enc"  # noqa: S105 - settings key name, not a secret
K_ALLOWED = "allowed_emails"
K_ADMINS = "admin_emails"
K_SETUP = "setup_code"

MAX_SETUP_FAILURES = 20
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9._-]+\.apps\.googleusercontent\.com$")
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


class ConfigError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class GoogleCreds:
    client_id: str
    client_secret: str


# ----------------------------------------------------------------------------- reads


def _get(s: Session, key: str) -> Any:
    row = s.get(Setting, key)
    return row.value if row is not None else None


def _set(s: Session, key: str, value: Any) -> None:
    row = s.get(Setting, key)
    if row is None:
        s.add(Setting(key=key, value=value))
    else:
        row.value = value


def _emails(values: Any) -> set[str]:
    if not values:
        return set()
    return {str(v).strip().lower() for v in values if str(v).strip()}


def google_env_managed() -> bool:
    cfg = get_config()
    return bool(cfg.google_client_id and cfg.google_client_secret)


def emails_env_managed() -> bool:
    return bool(get_config().allowed_emails)


def google_creds(s: Session | None = None) -> GoogleCreds | None:
    cfg = get_config()
    if google_env_managed():
        return GoogleCreds(cfg.google_client_id, cfg.google_client_secret)
    if s is None:
        with read_session() as rs:
            return google_creds(rs)
    cid, enc = _get(s, K_CLIENT_ID), _get(s, K_CLIENT_SECRET)
    if not cid or not enc:
        return None
    return GoogleCreds(str(cid), decrypt(str(enc)))


def allowed_emails(s: Session | None = None) -> set[str]:
    if emails_env_managed():
        return get_config().allowed_emails
    if s is None:
        with read_session() as rs:
            return allowed_emails(rs)
    return _emails(_get(s, K_ALLOWED))


def admin_emails(s: Session | None = None) -> set[str]:
    cfg = get_config()
    if cfg.admin_emails or emails_env_managed():
        return cfg.admin_emails
    if s is None:
        with read_session() as rs:
            return admin_emails(rs)
    return _emails(_get(s, K_ADMINS))


def is_allowed(email: str | None, s: Session | None = None) -> bool:
    return bool(email) and email.strip().lower() in allowed_emails(s)  # type: ignore[union-attr]


def needs_setup(s: Session | None = None) -> bool:
    return google_creds(s) is None or not allowed_emails(s)


def setup_status(s: Session | None = None) -> SetupStatus:
    cfg = get_config()
    return SetupStatus(
        needs_setup=needs_setup(s),
        google_configured=google_creds(s) is not None,
        emails_configured=bool(allowed_emails(s)),
        google_env_managed=google_env_managed(),
        emails_env_managed=emails_env_managed(),
        redirect_uri=cfg.redirect_uri,
        base_url=cfg.base_url,
    )


# ----------------------------------------------------------------------------- setup code


def new_setup_code(s: Session) -> str:
    """Issue a fresh one-time setup code (replaces any previous one). Returns the plaintext."""
    raw = "-".join("".join(secrets.choice(_CODE_ALPHABET) for _ in range(4)) for _ in range(3))
    _set(s, K_SETUP, {"hash": sha256_hex(raw), "issued_at": datetime.now(UTC).isoformat(), "failures": 0})
    s.flush()
    return raw


def _normalise_code(code: str) -> str:
    c = re.sub(r"[\s-]", "", code.upper())
    return "-".join(c[i : i + 4] for i in range(0, len(c), 4))


def verify_setup_code(s: Session, code: str) -> str | None:
    """Return None if ``code`` is the active setup code, else an error message.

    A wrong code increments the persisted failure counter (commit the session even on failure);
    after ``MAX_SETUP_FAILURES`` the code is dead until the next container start.
    """
    state = _get(s, K_SETUP)
    if not isinstance(state, dict) or not state.get("hash"):
        return "no setup code is active; restart the container for a new one"
    if int(state.get("failures", 0)) >= MAX_SETUP_FAILURES:
        return "too many wrong codes; restart the container for a new one"
    if not constant_time_equals(sha256_hex(_normalise_code(code)), str(state["hash"])):
        _set(s, K_SETUP, dict(state, failures=int(state.get("failures", 0)) + 1))
        s.flush()
        return "the setup code is not correct"
    return None


def _check_code(s: Session, code: str) -> None:
    err = verify_setup_code(s, code)
    if err:
        raise ConfigError("invalid_setup_code", err, 403)


# ----------------------------------------------------------------------------- validation & writes


def _clean_emails(values: list[str], field: str) -> list[str]:
    out: list[str] = []
    for v in values:
        e = v.strip().lower()
        if not e:
            continue
        if not _EMAIL_RE.match(e):
            raise ConfigError("validation_error", f"{field}: {v!r} is not a valid email address", 422)
        if e not in out:
            out.append(e)
    return out


def _clean_client_id(cid: str) -> str:
    cid = cid.strip()
    if not _CLIENT_ID_RE.match(cid):
        raise ConfigError("validation_error", "google_client_id must end with .apps.googleusercontent.com", 422)
    return cid


def _clean_secret(secret: str) -> str:
    secret = secret.strip()
    if len(secret) < 10 or any(c.isspace() for c in secret):
        raise ConfigError("validation_error", "google_client_secret looks invalid", 422)
    return secret


def complete_setup(s: Session, req: SetupRequest, ip: str | None = None) -> SetupStatus:
    """Validate the one-time code and store sign-in configuration. Caller commits."""
    if not needs_setup(s):
        raise ConfigError("already_configured", "DownTube is already set up", 409)
    _check_code(s, req.setup_code)

    if not google_env_managed():
        if not req.google_client_id or not req.google_client_secret:
            raise ConfigError("validation_error", "google_client_id and google_client_secret are required", 422)
        _set(s, K_CLIENT_ID, _clean_client_id(req.google_client_id))
        _set(s, K_CLIENT_SECRET, encrypt(_clean_secret(req.google_client_secret)))
    if not emails_env_managed():
        allowed = _clean_emails(req.allowed_emails, "allowed_emails")
        if not allowed:
            raise ConfigError("validation_error", "at least one allowed email is required", 422)
        admins = _clean_emails(req.admin_emails, "admin_emails") or allowed[:1]
        if not set(admins) <= set(allowed):
            raise ConfigError("validation_error", "every administrator must also be an allowed email", 422)
        _set(s, K_ALLOWED, allowed)
        _set(s, K_ADMINS, admins)
    row = s.get(Setting, K_SETUP)
    if row is not None:
        s.delete(row)  # single use
    s.add(
        AuditEvent(
            action="setup.complete",
            ip=ip,
            detail={"google_env": google_env_managed(), "emails_env": emails_env_managed()},
        )
    )
    s.flush()
    return setup_status(s)


def google_settings(s: Session) -> GoogleSettingsOut:
    creds = google_creds(s)
    return GoogleSettingsOut(
        google_client_id=creds.client_id if creds else None,
        google_client_secret_set=creds is not None,
        allowed_emails=sorted(allowed_emails(s)),
        admin_emails=sorted(admin_emails(s)),
        google_env_managed=google_env_managed(),
        emails_env_managed=emails_env_managed(),
        redirect_uri=get_config().redirect_uri,
    )


def update_google_settings(s: Session, upd: GoogleSettingsUpdate, actor_email: str, user_id: int) -> GoogleSettingsOut:
    changed: list[str] = []
    if upd.google_client_id is not None or upd.google_client_secret is not None:
        if google_env_managed():
            raise ConfigError("env_managed", "Google client credentials are set in the server environment", 409)
        if upd.google_client_id is not None:
            _set(s, K_CLIENT_ID, _clean_client_id(upd.google_client_id))
            changed.append("google_client_id")
        if upd.google_client_secret:
            _set(s, K_CLIENT_SECRET, encrypt(_clean_secret(upd.google_client_secret)))
            changed.append("google_client_secret")
    if upd.allowed_emails is not None or upd.admin_emails is not None:
        if emails_env_managed():
            raise ConfigError("env_managed", "allowed/admin emails are set in the server environment", 409)
        allowed = (
            _clean_emails(upd.allowed_emails, "allowed_emails")
            if upd.allowed_emails is not None
            else sorted(allowed_emails(s))
        )
        admins = (
            _clean_emails(upd.admin_emails, "admin_emails") if upd.admin_emails is not None else sorted(admin_emails(s))
        )
        me = actor_email.strip().lower()
        if me not in allowed or me not in admins:
            raise ConfigError("cannot_remove_self", "you cannot remove your own account or admin rights", 409)
        if not set(admins) <= set(allowed):
            raise ConfigError("validation_error", "every administrator must also be an allowed email", 422)
        _set(s, K_ALLOWED, allowed)
        _set(s, K_ADMINS, admins)
        changed += ["allowed_emails", "admin_emails"]
    if changed:
        s.add(AuditEvent(user_id=user_id, action="google_settings.update", detail={"changed": changed}))
    s.flush()
    return google_settings(s)
