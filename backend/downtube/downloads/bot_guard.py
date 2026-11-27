"""Global pause after YouTube's "confirm you're not a bot" check.

YouTube flags an IP address, not a single video. Retrying each job on its own schedule keeps the
queue walking through every other video, so the server keeps asking YouTube every few seconds and
the flag never clears. Instead, one bot check pauses *all* downloads:

* the first check pauses for ``BASE_PAUSE_S`` (30 min); each further check while the block lasts
  doubles it, up to ``MAX_PAUSE_S`` (12 h);
* checks that arrive while a pause is already running (other workers) do not escalate it;
* any successful probe clears the escalation.

The state lives in the ``settings`` table (key ``bot_check_pause``), so a restart does not reset
it. This never works around the check (no cookies, no sign-in): DownTube only waits.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from downtube.models import Setting

KEY = "bot_check_pause"
BASE_PAUSE_S = 30 * 60
MAX_PAUSE_S = 12 * 3600


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _state(s: Session) -> dict[str, Any]:
    row = s.get(Setting, KEY)
    return dict(row.value) if row is not None and isinstance(row.value, dict) else {}


def _save(s: Session, value: dict[str, Any]) -> None:
    row = s.get(Setting, KEY)
    if row is None:
        s.add(Setting(key=KEY, value=value))
    else:
        row.value = value


def paused_until(s: Session, now: datetime) -> datetime | None:
    """End of the current pause, or ``None`` when downloads may run."""
    until = _state(s).get("until")
    if not until:
        return None
    end = _aware(datetime.fromisoformat(until))
    return end if end > _aware(now) else None


def pause_seconds(strikes: int) -> float:
    return float(min(BASE_PAUSE_S * 2 ** max(strikes - 1, 0), MAX_PAUSE_S))


def record_bot_check(s: Session, now: datetime, rng: random.Random | None = None) -> datetime:
    """Register a bot check and return when downloads may resume."""
    current = paused_until(s, now)
    if current is not None:  # another worker already paused the queue
        return current
    strikes = int(_state(s).get("strikes", 0)) + 1
    jitter = (rng or random).uniform(0.9, 1.1)  # noqa: S311 - jitter only
    until = _aware(now) + timedelta(seconds=pause_seconds(strikes) * jitter)
    _save(s, {"until": until.isoformat(), "strikes": strikes, "since": _state(s).get("since") or now.isoformat()})
    return until


def record_success(s: Session) -> None:
    """A probe got through: clear the escalation."""
    if _state(s):
        _save(s, {})


def status(s: Session, now: datetime) -> tuple[datetime | None, int]:
    """(pause end or None, consecutive bot checks) for the dashboard."""
    return paused_until(s, now), int(_state(s).get("strikes", 0))
