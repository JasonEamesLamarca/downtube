from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from helpers import _reset_in_memory_state, login  # noqa: F401
from sqlalchemy import select

from downtube import runtime_status, scheduler
from downtube.config import get_config
from downtube.db import read_session, write_session
from downtube.models import ArchiveMode, Channel, ChannelRule, OAuthState, Subscription, SyncRun, SyncStatus, WebSession
from downtube.sync import runner


@pytest.fixture
def sched(client):
    yield scheduler
    scheduler.stop()


def test_start_stop_reschedule_and_health_flag(sched, client):
    h = login(client)
    with write_session() as s:
        s.add(SyncRun(user_id=h.user_id, kind="likes", status=SyncStatus.running))
    scheduler.start()
    try:
        assert runtime_status.snapshot()["scheduler"] is True
        names = {j.id for j in scheduler._scheduler.get_jobs()}
        assert names == {"likes_poll", "likes_full", "subscriptions", "channels", "metadata", "cleanup", "backup"}
        assert scheduler._scheduler.get_job("likes_poll").trigger.interval == timedelta(minutes=15)
        # every job is scheduled; none is left paused (next_run_time None = paused)
        assert all(j.next_run_time is not None for j in scheduler._scheduler.get_jobs())
        with read_session() as s:  # runs orphaned by a restart are closed
            assert s.scalar(select(SyncRun)).status == SyncStatus.error
        client.put("/api/settings", json={"likes_poll_minutes": 45, "channel_poll_minutes": 120}, headers=h)
        assert scheduler._scheduler.get_job("likes_poll").trigger.interval == timedelta(minutes=45)
        assert scheduler._scheduler.get_job("channels").trigger.interval == timedelta(minutes=120)
        scheduler.start()  # idempotent
    finally:
        scheduler.stop()
    assert runtime_status.snapshot()["scheduler"] is False
    scheduler.reschedule()  # no-op when stopped


def test_jobs_only_run_for_active_accounts(client, monkeypatch):
    a = login(client, "owner@example.com")
    b = login(client, "second@example.com", status="reauth_required")
    calls = []
    monkeypatch.setattr(runner, "run_blocking", lambda kind, uid, target=None, **kw: calls.append((kind, uid, target)))
    scheduler.job_likes_poll()
    scheduler.job_likes_full()
    scheduler.job_subscriptions()
    assert calls == [("likes", a.user_id, None), ("likes_full", a.user_id, None), ("subscriptions", a.user_id, None)]
    calls.clear()
    with write_session() as s:
        for uid, cid, mode, paused in [
            (a.user_id, "UC1", ArchiveMode.follow_new, False),
            (a.user_id, "UC2", ArchiveMode.follow_new, True),
            (a.user_id, "UC3", ArchiveMode.off, False),
            (b.user_id, "UC4", ArchiveMode.follow_new, False),
        ]:
            s.add(Channel(id=cid, title=cid))
            s.flush()
            s.add(Subscription(user_id=uid, channel_id=cid))
            s.add(ChannelRule(user_id=uid, channel_id=cid, mode=mode, paused=paused))
    scheduler.job_channels()
    assert calls == [("channel", a.user_id, "UC1")]


def test_channel_loop_stops_on_quota_deferral(client, monkeypatch):
    a = login(client)
    with write_session() as s:
        for cid in ("UC1", "UC2"):
            s.add(Channel(id=cid, title=cid))
            s.flush()
            s.add(ChannelRule(user_id=a.user_id, channel_id=cid, mode=ArchiveMode.follow_new))
    seen = []

    def fake(kind, uid, target=None, **kw):
        seen.append(target)
        return type("R", (), {"status": SyncStatus.quota_deferred})()

    monkeypatch.setattr(runner, "run_blocking", fake)
    scheduler.job_channels()
    assert len(seen) == 1


def test_backup_keeps_last_seven(client):
    login(client)
    backups = get_config().data_dir / "backups"
    backups.mkdir()
    for i in range(9):
        (backups / f"downtube-2020010{i + 1}-000000.db").write_bytes(b"old")
    dest = scheduler.backup_database()
    files = sorted(backups.glob("downtube-*.db"))
    assert len(files) == 7 and dest in files
    import sqlite3

    con = sqlite3.connect(dest)
    try:
        assert con.execute("select count(*) from users").fetchone()[0] == 1  # a consistent, usable copy
    finally:
        con.close()


def test_cleanup_removes_expired_sessions_and_oauth_states(client):
    h = login(client)
    old = datetime.now(UTC) - timedelta(days=1)
    with write_session() as s:
        s.add(OAuthState(state_hash="x" * 64, code_verifier_enc="v", nonce="n", expires_at=old))
        s.add(
            OAuthState(
                state_hash="y" * 64,
                code_verifier_enc="v",
                nonce="n",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
        stale = s.scalars(select(WebSession)).first()
        s.add(WebSession(id_hash="z" * 64, user_id=stale.user_id, csrf_token="c", expires_at=old))
    scheduler.job_cleanup()
    with read_session() as s:
        assert [o.state_hash[0] for o in s.scalars(select(OAuthState))] == ["y"]
        assert len(s.scalars(select(WebSession)).all()) == 1
    assert client.get("/api/auth/me", headers=h).status_code == 200


def test_kick_channel_is_noop_without_scheduler(client):
    scheduler.kick_channel(1, "UCx")  # must not raise
