from __future__ import annotations

from datetime import UTC, datetime

from helpers import (  # noqa: F401
    _reset_in_memory_state,  # noqa: F401
    fake_yt,
    login,
    quota_error,
    vid_id,
)
from sqlalchemy import select

from downtube.db import read_session, write_session
from downtube.downloads import jobs
from downtube.models import (
    DiscoverySource,
    DownloadJob,
    MediaFile,
    QuotaUsage,
    SyncCheckpoint,
    SyncStatus,
    User,
    Video,
    VideoSource,
)
from downtube.schemas import SettingsUpdate
from downtube.settings_service import update_settings
from downtube.sync.likes import sync_likes
from downtube.youtube import quota


def set_mode(mode: str) -> None:
    with write_session() as s:
        update_settings(s, SettingsUpdate(likes_mode=mode))


def sources(user_id: int, **kw):
    with read_session() as s:
        rows = s.scalars(select(VideoSource).where(VideoSource.user_id == user_id)).all()
        return {r.video_id: (r.active, r.archive_eligible, r.source) for r in rows}


def job_ids() -> dict[str, int]:
    """video id -> job priority"""
    with read_session() as s:
        return {j.video_id: j.priority for j in s.scalars(select(DownloadJob))}


def test_initial_scan_future_only_is_baseline(client, fake_yt):
    h = login(client)
    fake_yt.set_likes([vid_id(i) for i in range(1, 4)], [vid_id(i) for i in range(4, 6)])
    run = sync_likes(h.user_id, full=False, trigger="manual")
    assert run.status == SyncStatus.ok and run.items_seen == 5 and run.items_new == 5 and run.jobs_queued == 0
    assert fake_yt.count("videos") == 2  # initial scan walked both pages even though not "full"
    src = sources(h.user_id)
    assert len(src) == 5 and all(active and not eligible for active, eligible, _ in src.values())
    assert job_ids() == {}
    with read_session() as s:
        assert s.scalar(select(SyncCheckpoint).where(SyncCheckpoint.kind == "likes")).value["baselined"] is True
        assert s.get(Video, vid_id(1)).duration_s == 600 and s.get(Video, vid_id(1)).is_short is False
    assert run.quota_used == 2


def test_initial_scan_all_queues_jobs_with_like_priority(client, fake_yt):
    h = login(client)
    set_mode("all")
    fake_yt.set_likes([vid_id(1), vid_id(2)])
    run = sync_likes(h.user_id, trigger="manual")
    assert run.jobs_queued == 2
    assert set(job_ids().values()) == {jobs.PRIORITY_LIKE}


def test_incremental_detects_newly_liked_old_video(client, fake_yt):
    h = login(client)
    fake_yt.set_likes([vid_id(1), vid_id(2)])
    sync_likes(h.user_id, trigger="manual")  # baseline
    # an OLD video (published years ago) is liked now: it appears first in the list
    fake_yt.add_video("oldvideo001", published="2014-03-03T00:00:00Z")
    fake_yt.set_likes(["oldvideo001", vid_id(1), vid_id(2)])
    before = fake_yt.count("videos")
    run = sync_likes(h.user_id, trigger="schedule")
    assert run.items_new == 1 and run.jobs_queued == 1
    assert fake_yt.count("videos") - before == 1  # stopped after a page with a seen id? (single page)
    assert sources(h.user_id)["oldvideo001"][:2] == (True, True)
    assert "oldvideo001" in job_ids()
    # nothing new: incremental poll costs one call and queues nothing
    before = fake_yt.count("videos")
    run = sync_likes(h.user_id, trigger="schedule")
    assert run.items_new == 0 and run.jobs_queued == 0 and fake_yt.count("videos") - before == 1


def test_incremental_pages_until_a_page_has_no_new_ids_max_three(client, fake_yt):
    h = login(client)
    fake_yt.set_likes([vid_id(1)])
    sync_likes(h.user_id, trigger="manual")
    pages = [[vid_id(100 + p * 10 + i) for i in range(5)] for p in range(5)] + [[vid_id(1)]]
    fake_yt.set_likes(*pages)
    before = fake_yt.count("videos")
    run = sync_likes(h.user_id, trigger="schedule")
    assert fake_yt.count("videos") - before == 3 and run.items_new == 15


def test_full_reconcile_walks_all_pages_and_deactivates_removed_but_keeps_archive(client, fake_yt):
    h = login(client)
    set_mode("all")
    fake_yt.set_likes([vid_id(1), vid_id(2)], [vid_id(3)])
    sync_likes(h.user_id, trigger="manual")
    with write_session() as s:  # pretend video 2 was downloaded
        s.add(
            MediaFile(
                video_id=vid_id(2),
                root_path="/archive",
                rel_path="c/x.mp4",
                container="mp4",
                width=1,
                height=1,
                size_bytes=1,
            )
        )
    fake_yt.set_likes([vid_id(1)], [vid_id(3)])  # video 2 was unliked
    before = fake_yt.count("videos")
    run = sync_likes(h.user_id, full=True, trigger="manual")
    assert fake_yt.count("videos") - before == 2 and run.kind == "likes_full"
    src = sources(h.user_id)
    assert src[vid_id(2)][0] is False and src[vid_id(1)][0] and src[vid_id(3)][0]
    with read_session() as s:
        assert s.scalar(select(MediaFile).where(MediaFile.video_id == vid_id(2))) is not None
    # re-liking reactivates it
    fake_yt.set_likes([vid_id(2), vid_id(1)], [vid_id(3)])
    run = sync_likes(h.user_id, trigger="schedule")
    assert sources(h.user_id)[vid_id(2)][0] is True and run.items_new == 1


def test_quota_exceeded_defers_and_blocks_further_calls(client, fake_yt):
    h = login(client)
    fake_yt.set_likes([vid_id(1)])
    fake_yt.fail = [quota_error()]
    run = sync_likes(h.user_id, trigger="manual")
    assert run.status == SyncStatus.quota_deferred
    used, exhausted = quota.usage_today()
    assert exhausted and used == 1
    n = len(fake_yt.calls)
    run = sync_likes(h.user_id, trigger="manual")
    assert run.status == SyncStatus.quota_deferred and len(fake_yt.calls) == n  # no HTTP while exhausted
    with read_session() as s:  # no baseline checkpoint was written for the failed initial scan
        assert s.scalar(select(SyncCheckpoint)) is None
    with write_session() as s:  # next PT day
        s.get(QuotaUsage, quota.pt_day()).exhausted_at = None
        s.get(QuotaUsage, quota.pt_day()).units = 0
    assert sync_likes(h.user_id, trigger="manual").status == SyncStatus.ok


def test_budget_reached_marks_exhausted_without_http(client, fake_yt):
    h = login(client)
    with write_session() as s:
        update_settings(s, SettingsUpdate(quota_daily_budget=100))
        s.add(QuotaUsage(day=quota.pt_day(), units=100))
    run = sync_likes(h.user_id, trigger="manual")
    assert run.status == SyncStatus.quota_deferred and fake_yt.calls == []
    assert quota.info().exhausted is True


def test_unacknowledged_user_discovers_but_never_queues(client, fake_yt):
    h = login(client, acknowledged=False)
    set_mode("all")
    fake_yt.set_likes([vid_id(1)])
    run = sync_likes(h.user_id, trigger="manual")
    assert run.items_new == 1 and run.jobs_queued == 0
    assert sources(h.user_id)[vid_id(1)][1] is False


def test_upcoming_live_is_not_queued_until_completed(client, fake_yt):
    h = login(client)
    set_mode("all")
    fake_yt.add_video(
        "live0000001", duration="P0D", broadcast="upcoming", live={"scheduledStartTime": "2026-12-01T00:00:00Z"}
    )
    fake_yt.set_likes(["live0000001"])
    run = sync_likes(h.user_id, trigger="manual")
    assert run.jobs_queued == 0 and job_ids() == {}
    with read_session() as s:
        assert s.get(Video, "live0000001").live_status == "upcoming"
    # the stream finishes; the next poll's pending-live resolution queues it
    fake_yt.add_video(
        "live0000001",
        duration="PT2H",
        live={"actualStartTime": "2026-12-01T00:00:00Z", "actualEndTime": "2026-12-01T02:00:00Z"},
    )
    run = sync_likes(h.user_id, trigger="schedule")
    assert run.jobs_queued == 1 and "live0000001" in job_ids()
    with read_session() as s:
        v = s.get(Video, "live0000001")
        assert v.live_status == "completed" and v.is_live is True and v.duration_s == 7200


def test_likes_mode_off_does_nothing(client, fake_yt):
    h = login(client)
    set_mode("off")
    fake_yt.set_likes([vid_id(1)])
    run = sync_likes(h.user_id, trigger="manual")
    assert run.status == SyncStatus.ok and fake_yt.calls == [] and sources(h.user_id) == {}


def test_manual_flag_unused():
    assert DiscoverySource.like.value == "like" and User and datetime and UTC
