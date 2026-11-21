"""Quota accounting. YouTube's daily quota resets at midnight America/Los_Angeles."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from downtube.db import read_session, write_session
from downtube.models import QuotaUsage
from downtube.schemas import QuotaInfo
from downtube.settings_service import get_value

PT = ZoneInfo("America/Los_Angeles")


def pt_day(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).astimezone(PT).strftime("%Y-%m-%d")


def next_reset(now: datetime | None = None) -> datetime:
    local = (now or datetime.now(UTC)).astimezone(PT)
    midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(UTC)


def budget() -> int:
    with read_session() as s:
        return int(get_value(s, "quota_daily_budget"))


def usage_today() -> tuple[int, bool]:
    with read_session() as s:
        row = s.get(QuotaUsage, pt_day())
        return (row.units, row.exhausted_at is not None) if row else (0, False)


def record(units: int) -> int:
    """Add ``units`` to today's counter; return the new total."""
    with write_session() as s:
        day = pt_day()
        row = s.get(QuotaUsage, day)
        if row is None:
            row = QuotaUsage(day=day, units=0)
            s.add(row)
        row.units += units
        return row.units


def mark_exhausted() -> None:
    with write_session() as s:
        day = pt_day()
        row = s.get(QuotaUsage, day)
        if row is None:
            row = QuotaUsage(day=day, units=0)
            s.add(row)
        if row.exhausted_at is None:
            row.exhausted_at = datetime.now(UTC)


def info() -> QuotaInfo:
    with read_session() as s:
        row = s.get(QuotaUsage, pt_day())
        b = int(get_value(s, "quota_daily_budget"))
        used = row.units if row else 0
        flagged = row is not None and row.exhausted_at is not None
    exhausted = flagged or used >= b
    return QuotaInfo(day=pt_day(), used=used, budget=b, exhausted=exhausted, resets_at=next_reset())
