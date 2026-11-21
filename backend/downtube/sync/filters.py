"""Channel-rule filters (pure)."""

from __future__ import annotations

from datetime import UTC, datetime

from downtube.models import ChannelRule, Video


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def passes_filters(v: Video, rule: ChannelRule) -> bool:
    """Shorts / live / published_after / max_duration. Unknown values never exclude."""
    if v.is_short and not rule.include_shorts:
        return False
    if v.is_live and not rule.include_live:
        return False
    pa, pub = _aware(rule.published_after), _aware(v.published_at)
    if pa is not None and pub is not None and pub < pa:
        return False
    return not (rule.max_duration_s is not None and v.duration_s is not None and v.duration_s > rule.max_duration_s)
