"""Runs sync jobs with a per-(kind, user, target) guard against concurrent runs.

Manual API triggers return the SyncRun immediately and execute in a background thread. With
``DOWNTUBE_DISABLE_BACKGROUND=1`` (tests) the work runs inline instead.
"""

from __future__ import annotations

import logging
import threading

from sqlalchemy import select

from downtube.config import get_config
from downtube.db import read_session
from downtube.models import SyncRun, SyncStatus
from downtube.sync import common

log = logging.getLogger("downtube.sync")

_active: set[tuple[str, int, str]] = set()
_guard = threading.Lock()

KINDS = ("likes", "likes_full", "subscriptions", "channel")


def _key(kind: str, user_id: int, target: str | None) -> tuple[str, int, str]:
    return ("likes" if kind.startswith("likes") else kind, user_id, target or "")


def _execute(kind: str, user_id: int, target: str | None, run_id: int, trigger: str) -> None:
    key = _key(kind, user_id, target)
    try:
        if kind in ("likes", "likes_full"):
            from downtube.sync.likes import sync_likes

            sync_likes(user_id, full=kind == "likes_full", trigger=trigger, run_id=run_id)
        elif kind == "subscriptions":
            from downtube.sync.subscriptions import sync_subscriptions

            sync_subscriptions(user_id, trigger, run_id=run_id)
        elif kind == "channel":
            from downtube.sync.channels import sync_channel

            sync_channel(user_id, target or "", trigger, run_id=run_id)
        else:
            raise ValueError(f"unknown sync kind {kind}")
    except Exception:  # noqa: BLE001
        log.exception("sync %s crashed", kind)
        try:
            common.finish_run(run_id, SyncStatus.error, error="internal error (see logs)")
        except Exception:  # noqa: BLE001
            log.exception("could not record failure of sync run %s", run_id)
    finally:
        with _guard:
            _active.discard(key)


def _running_run(kind: str, user_id: int, target: str | None) -> SyncRun | None:
    kinds = ("likes", "likes_full") if kind.startswith("likes") else (kind,)
    with read_session() as s:
        run = s.scalar(
            select(SyncRun)
            .where(
                SyncRun.user_id == user_id,
                SyncRun.kind.in_(kinds),
                SyncRun.status == SyncStatus.running,
                SyncRun.target == target if target else SyncRun.target.is_(None),
            )
            .order_by(SyncRun.id.desc())
        )
        if run is not None:
            s.expunge(run)
        return run


def start(kind: str, user_id: int, target: str | None = None, *, trigger: str = "manual") -> SyncRun:
    """Start a sync (background thread). If the same kind/user/target is already running, return that run."""
    key = _key(kind, user_id, target)
    with _guard:
        if key in _active:
            existing = _running_run(kind, user_id, target)
            if existing is not None:
                return existing
        _active.add(key)
    run_id = common.create_run(kind, user_id, target, trigger)
    if get_config().disable_background:
        _execute(kind, user_id, target, run_id, trigger)
    else:
        threading.Thread(
            target=_execute, args=(kind, user_id, target, run_id, trigger), name=f"sync-{kind}", daemon=True
        ).start()
    return common.get_run(run_id)


def run_blocking(kind: str, user_id: int, target: str | None = None, *, trigger: str = "schedule") -> SyncRun | None:
    """For the scheduler: run inline; None when an identical sync is already in flight."""
    key = _key(kind, user_id, target)
    with _guard:
        if key in _active:
            return None
        _active.add(key)
    run_id = common.create_run(kind, user_id, target, trigger)
    _execute(kind, user_id, target, run_id, trigger)
    return common.get_run(run_id)


def reset_guard() -> None:
    with _guard:
        _active.clear()
