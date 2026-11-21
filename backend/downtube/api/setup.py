"""First-run setup (no auth, one-time code) and admin-editable Google sign-in settings."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from downtube import app_config
from downtube.auth.deps import require_admin
from downtube.auth.ratelimit import RateLimiter, client_ip
from downtube.db import read_session, write_session
from downtube.main import ApiError
from downtube.models import User
from downtube.schemas import GoogleSettingsOut, GoogleSettingsUpdate, SetupRequest, SetupStatus

log = logging.getLogger("downtube.setup")
router = APIRouter(tags=["setup"])
setup_limiter = RateLimiter(limit=10, window_s=60.0)


def _raise(e: app_config.ConfigError) -> ApiError:
    return ApiError(e.status, e.code, e.message)


@router.get("/setup/status", response_model=SetupStatus)
def setup_status() -> SetupStatus:
    with read_session() as s:
        return app_config.setup_status(s)


@router.post("/setup", response_model=SetupStatus)
def complete_setup(body: SetupRequest, request: Request) -> SetupStatus:
    ip = client_ip(request)
    if not setup_limiter.check(ip):
        raise ApiError(429, "rate_limited", "too many setup attempts, wait a minute")
    with write_session() as s:  # committed on its own so the failure counter persists
        if not app_config.needs_setup(s):
            raise ApiError(409, "already_configured", "DownTube is already set up")
        err = app_config.verify_setup_code(s, body.setup_code)
    if err:
        log.warning("setup: invalid setup code from %s", ip)
        raise ApiError(403, "invalid_setup_code", err)
    try:
        with write_session() as s:
            status = app_config.complete_setup(s, body, ip)
    except app_config.ConfigError as e:
        raise _raise(e) from e
    log.info("setup completed from %s", ip)
    return status


@router.get("/admin/google", response_model=GoogleSettingsOut)
def get_google(user: User = Depends(require_admin)) -> GoogleSettingsOut:
    with read_session() as s:
        return app_config.google_settings(s)


@router.put("/admin/google", response_model=GoogleSettingsOut)
def put_google(body: GoogleSettingsUpdate, user: User = Depends(require_admin)) -> GoogleSettingsOut:
    try:
        with write_session() as s:
            out = app_config.update_google_settings(s, body, user.email, user.id)
            if body.admin_emails is not None:
                admins = set(out.admin_emails)
                for u in s.query(User).all():
                    u.is_admin = u.email.lower() in admins
            return out
    except app_config.ConfigError as e:
        raise _raise(e) from e
