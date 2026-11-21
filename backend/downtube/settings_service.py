"""Application settings stored in the ``settings`` table, validated through ``SettingsOut``."""

from __future__ import annotations

import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from downtube.config import get_config
from downtube.models import AuditEvent, Setting
from downtube.schemas import RESOLUTION_CAPS, SettingsOut, SettingsUpdate

_WINDOW_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")


def defaults() -> dict[str, Any]:
    cfg = get_config()
    return {
        "archive_root": cfg.archive_root,
        "likes_mode": "future_only",
        "likes_poll_minutes": 15,
        "likes_full_reconcile_hours": 24,
        "likes_max_height": None,  # best available (≤2160)
        "subscriptions_refresh_hours": 24,
        "channel_poll_minutes": 60,
        "default_max_height": None,
        "audio_preference": "aac",
        "prefer_hdr": False,
        "compatibility_profile": False,
        "mkv_fallback": False,
        "max_concurrent_downloads": 2,
        "bandwidth_limit_kbps": None,
        "download_window": None,
        "min_free_gb": 100,
        "large_channel_threshold": 200,
        "write_thumbnails": True,
        "write_nfo": False,
        "keep_superseded_copies": True,
        "quota_daily_budget": 9000,
        "timezone": "UTC",
    }


class SettingsError(ValueError):
    pass


def _load(s: Session) -> dict[str, Any]:
    values = defaults()
    for row in s.scalars(select(Setting)):
        if row.key in values:
            values[row.key] = row.value
    return values


def get_settings(s: Session) -> SettingsOut:
    return SettingsOut.model_validate(_load(s))


def get_value(s: Session, key: str) -> Any:
    return _load(s)[key]


def _validate(key: str, value: Any) -> Any:
    if key in ("likes_max_height", "default_max_height") and value is not None and value not in RESOLUTION_CAPS:
        raise SettingsError(f"{key} must be one of {RESOLUTION_CAPS} or null")
    if key == "download_window":
        if value in ("", None):
            return None
        if not _WINDOW_RE.match(value):
            raise SettingsError("download_window must look like HH:MM-HH:MM")
    if key == "bandwidth_limit_kbps" and value == 0:
        return None
    if key == "timezone":
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise SettingsError(f"unknown timezone {value!r}") from e
    if key == "archive_root":
        # Resolved & checked by storage.paths at the API layer; here only basic sanity.
        if not str(value).startswith("/"):
            raise SettingsError("archive_root must be an absolute container path")
    return value


def update_settings(s: Session, upd: SettingsUpdate, user_id: int | None = None) -> SettingsOut:
    """Persist explicitly provided fields. ``None`` for resolution caps means "best available"."""
    changes = upd.model_dump(exclude_unset=True)
    current = _load(s)
    applied: dict[str, Any] = {}
    for key, value in changes.items():
        if value is None and key not in (
            "likes_max_height",
            "default_max_height",
            "download_window",
            "bandwidth_limit_kbps",
        ):
            continue
        value = _validate(key, value)
        if current.get(key) == value:
            continue
        row = s.get(Setting, key)
        if row is None:
            s.add(Setting(key=key, value=value))
        else:
            row.value = value
        applied[key] = value
        current[key] = value
    out = SettingsOut.model_validate(current)  # validates the merged result
    if applied:
        s.add(AuditEvent(user_id=user_id, action="settings.update", detail=applied))
    s.flush()
    return out
