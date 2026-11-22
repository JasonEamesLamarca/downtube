"""Background scheduler (APScheduler 3, BackgroundScheduler, UTC).

Jobs only run for accounts whose status is ``active``. ``reschedule()`` re-reads the intervals from
settings; call it after any settings change.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from downtube import app_config, runtime_status
from downtube.auth.sessions import cleanup_expired
from downtube.config import get_config
from downtube.db import read_session, write_session
from downtube.models import AccountStatus, ArchiveMode, ChannelRule, GoogleAccount, SyncRun, SyncStatus, User
from downtube.settings_service import get_settings
from downtube.sync import common, runner

log = logging.getLogger("downtube.scheduler")

BACKUPS_KEPT = 7
_scheduler: BackgroundScheduler | None = None
_lock = threading.Lock()


# --------------------------------------------------------------------------- job bodies


def active_user_ids() -> list[int]:
    """Users with a working Google connection who are still on the allowlist."""
    with read_session() as s:
        rows = s.execute(
            select(GoogleAccount.user_id, User.email)
            .join(User, User.id == GoogleAccount.user_id)
            .where(GoogleAccount.status == AccountStatus.active)
            .order_by(GoogleAccount.user_id)
        ).all()
    allowed = app_config.allowed_emails()
    return [uid for uid, email in rows if email and email.lower() in allowed]


def job_likes_poll() -> None:
    for uid in active_user_ids():
        runner.run_blocking("likes", uid)


def job_likes_full() -> None:
    for uid in active_user_ids():
        runner.run_blocking("likes_full", uid)


def job_subscriptions() -> None:
    for uid in active_user_ids():
        runner.run_blocking("subscriptions", uid)


def job_channels() -> None:
    users = set(active_user_ids())
    with read_session() as s:
        rules = [
            (r.user_id, r.channel_id)
            for r in s.scalars(
                select(ChannelRule)
                .where(ChannelRule.mode != ArchiveMode.off, ChannelRule.paused.is_(False))
                .order_by(ChannelRule.last_sync_at.is_(None).desc(), ChannelRule.last_sync_at)
            )
            if r.user_id in users and not (r.mode == ArchiveMode.backfill_once and r.backfill_completed_at)
        ]
    for uid, cid in rules:
        run = runner.run_blocking("channel", uid, cid)
        if run is not None and run.status == SyncStatus.quota_deferred:
            log.info("quota exhausted; deferring remaining channel syncs until the next window")
            break


def job_metadata() -> None:
    from downtube.sync.metadata import refresh_stale_metadata

    refresh_stale_metadata(25)


def job_cleanup() -> None:
    with write_session() as s:
        sessions, states = cleanup_expired(s)
    if sessions or states:
        log.info("cleanup removed %d sessions, %d oauth states", sessions, states)


def backup_database(keep: int = BACKUPS_KEPT) -> Path | None:
    """Consistent online backup with the sqlite3 backup API; keeps the newest ``keep`` files."""
    cfg = get_config()
    if not cfg.db_path.exists():
        return None
    dest_dir = cfg.data_dir / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"downtube-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.db"
    src = sqlite3.connect(str(cfg.db_path))
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    for old in sorted(dest_dir.glob("downtube-*.db"))[:-keep]:
        old.unlink(missing_ok=True)
    return dest


def kick_channel(user_id: int, channel_id: str) -> None:
    """Sync one channel shortly (after a rule change). No-op when the scheduler is not running."""
    sch = _scheduler
    if sch is None or not sch.running:
        return
    sch.add_job(
        runner.run_blocking,
        args=("channel", user_id, channel_id),
        kwargs={"trigger": "manual"},
        next_run_time=datetime.now(UTC) + timedelta(seconds=2),
        id=f"kick-{user_id}-{channel_id}",
        replace_existing=True,
        misfire_grace_time=300,
    )


# --------------------------------------------------------------------------- lifecycle


def _triggers() -> dict[str, IntervalTrigger | CronTrigger]:
    with read_session() as s:
        st = get_settings(s)
    return {
        "likes_poll": IntervalTrigger(minutes=st.likes_poll_minutes, timezone=UTC),
        "likes_full": IntervalTrigger(hours=st.likes_full_reconcile_hours, timezone=UTC),
        "subscriptions": IntervalTrigger(hours=st.subscriptions_refresh_hours, timezone=UTC),
        "channels": IntervalTrigger(minutes=st.channel_poll_minutes, timezone=UTC),
        "metadata": CronTrigger(hour=4, minute=15, timezone=UTC),
        "cleanup": IntervalTrigger(hours=1, timezone=UTC),
        "backup": CronTrigger(hour=3, minute=30, timezone=UTC),
    }


_FUNCS = {
    "likes_poll": job_likes_poll,
    "likes_full": job_likes_full,
    "subscriptions": job_subscriptions,
    "channels": job_channels,
    "metadata": job_metadata,
    "cleanup": job_cleanup,
    "backup": backup_database,
}
# First run shortly after start (seconds); the rest wait for their interval.
_STARTUP_DELAY = {"subscriptions": 20, "likes_poll": 40, "channels": 120, "cleanup": 5}


def _mark_interrupted() -> None:
    with write_session() as s:
        for run in s.scalars(select(SyncRun).where(SyncRun.status == SyncStatus.running)):
            run.status = SyncStatus.error
            run.error = "interrupted by restart"
            run.finished_at = common.now()


def start() -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None and _scheduler.running:
            return
        _mark_interrupted()
        runner.reset_guard()
        sch = BackgroundScheduler(
            timezone=UTC, job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 900}
        )
        now = datetime.now(UTC)
        for name, trig in _triggers().items():
            delay = _STARTUP_DELAY.get(name)
            # Passing next_run_time=None to APScheduler adds the job *paused*; leave it out so the
            # trigger computes the first run.
            first_run = {"next_run_time": now + timedelta(seconds=delay)} if delay is not None else {}
            sch.add_job(_FUNCS[name], trig, id=name, name=name, replace_existing=True, **first_run)
        sch.start()
        _scheduler = sch
        runtime_status.set_flag("scheduler", True)
        log.info("scheduler started with %d jobs", len(sch.get_jobs()))


def stop() -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None:
            try:
                _scheduler.shutdown(wait=False)
            except Exception:  # noqa: BLE001
                log.exception("scheduler shutdown failed")
        _scheduler = None
        runtime_status.set_flag("scheduler", False)


def reschedule() -> None:
    """Apply changed intervals from settings (no-op when the scheduler is not running)."""
    sch = _scheduler
    if sch is None or not sch.running:
        return
    for name, trig in _triggers().items():
        if name in ("likes_poll", "likes_full", "subscriptions", "channels"):
            if sch.get_job(name) is None:
                sch.add_job(_FUNCS[name], trig, id=name, name=name, replace_existing=True)
            else:
                sch.reschedule_job(name, trigger=trig)
