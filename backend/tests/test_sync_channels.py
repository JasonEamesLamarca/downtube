from __future__ import annotations

from datetime import UTC, datetime, timedelta

from helpers import _reset_in_memory_state, fake_yt, login, quota_error, vid_id  # noqa: F401
from sqlalchemy import select

from downtube.db import read_session, write_session
from downtube.downloads import jobs
from downtube.models import (
    ArchiveMode,
    Availability,
    Channel,
    ChannelRule,
    DownloadJob,
    MediaFile,
    QuotaUsage,
    Subscription,
    SyncStatus,
    Video,
    VideoSource,
)
from downtube.schemas import SettingsUpdate
from downtube.settings_service import update_settings
from downtube.sync.channels import estimate_channel, sync_channel
from downtube.sync.metadata import refresh_stale_metadata
from downtube.sync.subscriptions import sync_subscriptions
from downtube.youtube import quota
from downtube.youtube.parse import parse_duration

CH = "UCchan1"


def make_rule(user_id, channel=CH, **kw):
    with write_session() as s:
        if s.get(Channel, channel) is None:
            s.add(Channel(id=channel, title=channel, uploads_playlist_id="UU" + channel[2:], video_count=10))
            s.flush()
        s.add(Subscription(user_id=user_id, channel_id=channel))
        kw.setdefault("mode", ArchiveMode.backfill_once)
        kw.setdefault("activated_at", datetime.now(UTC))
        s.add(ChannelRule(user_id=user_id, channel_id=channel, **kw))


def rule(user_id, channel=CH):
    with read_session() as s:
        r = s.scalar(select(ChannelRule).where(ChannelRule.user_id == user_id, ChannelRule.channel_id == channel))
        return dict(token=r.backfill_page_token, done=r.backfill_completed_at, status=r.sync_status, err=r.last_error)


def jobs_map():
    with read_session() as s:
        return {j.video_id: (j.priority, j.max_height) for j in s.scalars(select(DownloadJob))}


def src_map(user_id):
    with read_session() as s:
        return {
            v.video_id: (v.active, v.archive_eligible)
            for v in s.scalars(select(VideoSource).where(VideoSource.user_id == user_id))
        }


def test_parse_duration():
    assert parse_duration("PT1H2M3S") == 3723
    assert parse_duration("PT45S") == 45 and parse_duration("PT3M") == 180
    assert parse_duration("P1DT1H") == 90000 and parse_duration("P0D") == 0
    assert parse_duration("garbage") is None and parse_duration(None) is None


def test_subscriptions_sync_enriches_channels_and_marks_unsubscribed(client, fake_yt):
    h = login(client)
    fake_yt.add_channel("UCa", title="Alpha", video_count=42)
    fake_yt.add_subscription_page("UCa", "UCb")
    fake_yt.add_subscription_page("UCc")
    run = sync_subscriptions(h.user_id, "manual")
    assert run.status == SyncStatus.ok and run.items_seen == 3 and run.items_new == 3
    assert run.quota_used == 3  # 2 subscription pages + 1 channels.list batch
    with read_session() as s:
        a = s.get(Channel, "UCa")
        assert (a.title, a.video_count, a.uploads_playlist_id) == ("Alpha", 42, "UUa")
        assert s.scalar(select(ChannelRule)) is None  # no rule is created automatically
    # UCb is unsubscribed upstream
    fake_yt.subscriptions = []
    fake_yt.add_subscription_page("UCa", "UCc")
    sync_subscriptions(h.user_id, "manual")
    with read_session() as s:
        active = {x.channel_id: x.active for x in s.scalars(select(Subscription))}
    assert active == {"UCa": True, "UCb": False, "UCc": True}


def test_estimate_variants_and_large_flag(client):
    h = login(client)
    with write_session() as s:
        s.add(Channel(id="UCbig", title="Big", video_count=500))
        s.add(Channel(id="UCnone", title="NoCount"))
        s.add(Channel(id="UCdur", title="Dur", video_count=100))
        s.add(Video(id="durvid00001", channel_id="UCdur", duration_s=600))
        s.add(Channel(id="UCsample", title="Sample", video_count=10))
        s.add(Video(id="sample00001", channel_id="UCsample", duration_s=600))
        s.flush()
        s.add(
            MediaFile(
                video_id="sample00001",
                root_path="/a",
                rel_path="x",
                container="mp4",
                width=1,
                height=1,
                size_bytes=1000,
            )
        )
    big = estimate_channel(h.user_id, "UCbig")
    assert big.is_large and big.requires_confirmation and big.estimated_bytes is None
    assert "cannot be estimated" in big.estimate_basis
    none = estimate_channel(h.user_id, "UCnone")
    assert none.video_count is None and none.estimated_bytes is None and not none.requires_confirmation
    dur = estimate_channel(h.user_id, "UCdur", 1080)
    assert dur.estimated_bytes == int(100 * 600 * 6_000_000 / 8) and "1080p" in dur.estimate_basis
    sample = estimate_channel(h.user_id, "UCsample")
    assert sample.estimated_bytes == 10_000 and "archived" in sample.estimate_basis and not sample.is_large


def test_backfill_walks_all_pages_with_backfill_priority(client, fake_yt):
    h = login(client)
    make_rule(h.user_id)
    fake_yt.set_uploads(CH, [vid_id(1), vid_id(2)], [vid_id(3), vid_id(4)], [vid_id(5)])
    run = sync_channel(h.user_id, CH, "manual")
    assert run.status == SyncStatus.ok and run.items_seen == 5 and run.jobs_queued == 5
    assert {p for p, _ in jobs_map().values()} == {jobs.PRIORITY_BACKFILL}
    assert rule(h.user_id)["done"] is not None and rule(h.user_id)["token"] is None
    assert fake_yt.count("playlistItems") == 3 and fake_yt.count("videos") == 3  # one videos.list per page
    # backfill_once is finished: syncing again makes no API calls
    n = len(fake_yt.calls)
    assert sync_channel(h.user_id, CH, "schedule").status == SyncStatus.ok and len(fake_yt.calls) == n


def test_backfill_resumes_from_page_token_after_quota_deferral(client, fake_yt):
    h = login(client)
    make_rule(h.user_id)
    fake_yt.set_uploads(CH, [vid_id(1)], [vid_id(2)], [vid_id(3)])
    state = {"armed": True}

    def fail_page2(path, q):
        if path == "playlistItems" and q.get("pageToken") == "p1" and state["armed"]:
            return quota_error()

    fake_yt.fail_when = fail_page2
    run = sync_channel(h.user_id, CH, "manual")
    assert run.status == SyncStatus.quota_deferred and run.jobs_queued == 1
    assert rule(h.user_id)["token"] == "p1" and rule(h.user_id)["done"] is None
    assert rule(h.user_id)["status"] == SyncStatus.quota_deferred
    # quota resets (next PT day) and the sync resumes at p1, not from the start
    state["armed"] = False
    with write_session() as s:
        s.delete(s.get(QuotaUsage, quota.pt_day()))
    start = len(fake_yt.calls)
    run = sync_channel(h.user_id, CH, "schedule")
    assert run.status == SyncStatus.ok and run.jobs_queued == 2
    tokens = [q.get("pageToken") for p, q in fake_yt.calls[start:] if p == "playlistItems"]
    assert tokens == ["p1", "p2"]
    assert set(jobs_map()) == {vid_id(1), vid_id(2), vid_id(3)} and rule(h.user_id)["done"] is not None


def test_channel_sync_stops_at_90_percent_of_budget(client, fake_yt):
    h = login(client)
    make_rule(h.user_id)
    fake_yt.set_uploads(CH, [vid_id(1)])
    with write_session() as s:
        update_settings(s, SettingsUpdate(quota_daily_budget=1000))
        s.add(QuotaUsage(day=quota.pt_day(), units=900))
    run = sync_channel(h.user_id, CH, "schedule")
    assert run.status == SyncStatus.quota_deferred and fake_yt.calls == []
    assert quota.usage_today()[1] is False  # likes polling may still use the last 10 %


def test_filters_shorts_live_duration_published_after(client, fake_yt):
    h = login(client)
    make_rule(
        h.user_id,
        include_shorts=False,
        include_live=False,
        max_duration_s=3600,
        published_after=datetime(2025, 1, 1, tzinfo=UTC),
    )
    fake_yt.add_video("good0000001", channel=CH, duration="PT10M", published="2026-02-01T00:00:00Z")
    fake_yt.add_video("short000001", channel=CH, duration="PT45S", published="2026-02-01T00:00:00Z")
    fake_yt.add_video("short180001", channel=CH, duration="PT3M", published="2026-02-01T00:00:00Z")
    fake_yt.add_video("justover001", channel=CH, duration="PT3M1S", published="2026-02-01T00:00:00Z")
    fake_yt.add_video(
        "live0000001",
        channel=CH,
        duration="PT30M",
        published="2026-02-01T00:00:00Z",
        live={"actualStartTime": "2026-02-01T00:00:00Z", "actualEndTime": "2026-02-01T00:30:00Z"},
    )
    fake_yt.add_video("toolong0001", channel=CH, duration="PT2H", published="2026-02-01T00:00:00Z")
    fake_yt.add_video("old00000001", channel=CH, duration="PT10M", published="2020-02-01T00:00:00Z")
    fake_yt.set_uploads(
        CH, ["good0000001", "short000001", "short180001", "justover001", "live0000001", "toolong0001", "old00000001"]
    )
    run = sync_channel(h.user_id, CH, "manual")
    assert run.jobs_queued == 2
    assert set(jobs_map()) == {"good0000001", "justover001"}
    srcs = src_map(h.user_id)
    assert srcs["short000001"] == (True, False) and srcs["short180001"] == (True, False)
    assert srcs["live0000001"] == (True, False) and srcs["toolong0001"] == (True, False)
    assert srcs["old00000001"] == (True, False)
    with read_session() as s:
        assert s.get(Video, "short180001").is_short is True and s.get(Video, "justover001").is_short is False
        assert s.get(Video, "live0000001").is_live is True


def test_follow_new_only_archives_uploads_after_activation_and_stops_early(client, fake_yt):
    h = login(client)
    make_rule(h.user_id, mode=ArchiveMode.follow_new, activated_at=datetime(2026, 6, 1, tzinfo=UTC))
    fake_yt.add_video("new00000001", channel=CH, published="2026-07-01T00:00:00Z")
    fake_yt.add_video("old00000001", channel=CH, published="2026-01-01T00:00:00Z")
    fake_yt.add_video("old00000002", channel=CH, published="2025-01-01T00:00:00Z")
    fake_yt.set_uploads(CH, ["new00000001", "old00000001"], ["old00000002"])
    run = sync_channel(h.user_id, CH, "schedule")
    assert run.jobs_queued == 1 and jobs_map() == {"new00000001": (jobs.PRIORITY_SUBSCRIPTION, 2160)}
    assert "old00000001" not in src_map(h.user_id)
    assert fake_yt.count("playlistItems") == 2
    # next poll: head page only contains known/older items -> a single playlist call, no new work
    n = fake_yt.count("playlistItems")
    run = sync_channel(h.user_id, CH, "schedule")
    assert run.jobs_queued == 0 and fake_yt.count("playlistItems") - n == 1
    # a fresh upload is picked up
    fake_yt.add_video("new00000002", channel=CH, published="2026-08-01T00:00:00Z")
    fake_yt.set_uploads(CH, ["new00000002", "new00000001", "old00000001"])
    run = sync_channel(h.user_id, CH, "schedule")
    assert run.jobs_queued == 1 and "new00000002" in jobs_map()


def test_cancelled_jobs_are_not_requeued_on_every_poll(client, fake_yt):
    h = login(client)
    make_rule(h.user_id, mode=ArchiveMode.follow_new, activated_at=datetime(2026, 6, 1, tzinfo=UTC))
    fake_yt.add_video("new00000001", channel=CH, published="2026-07-01T00:00:00Z")
    fake_yt.set_uploads(CH, ["new00000001"])
    sync_channel(h.user_id, CH, "schedule")
    with write_session() as s:
        j = s.scalar(select(DownloadJob))
        jobs.cancel_job(s, j)
    assert sync_channel(h.user_id, CH, "schedule").jobs_queued == 0
    with read_session() as s:
        assert len(s.scalars(select(DownloadJob)).all()) == 1


def test_backfill_and_follow_does_both(client, fake_yt):
    h = login(client)
    make_rule(h.user_id, mode=ArchiveMode.backfill_and_follow, activated_at=datetime(2026, 6, 1, tzinfo=UTC))
    fake_yt.add_video("new00000001", channel=CH, published="2026-07-01T00:00:00Z")
    fake_yt.add_video("old00000001", channel=CH, published="2026-01-01T00:00:00Z")
    fake_yt.set_uploads(CH, ["new00000001"], ["old00000001"])
    run = sync_channel(h.user_id, CH, "manual")
    assert run.jobs_queued == 2
    # the video reached by follow gets subscription priority; old one is backfill
    pr = {v: p for v, (p, _) in jobs_map().items()}
    assert pr["new00000001"] == jobs.PRIORITY_SUBSCRIPTION and pr["old00000001"] == jobs.PRIORITY_BACKFILL


def test_like_and_subscription_share_one_job(client, fake_yt):
    from downtube.sync.likes import sync_likes

    h = login(client)
    with write_session() as s:
        update_settings(s, SettingsUpdate(likes_mode="all", default_max_height=720))
    make_rule(h.user_id, max_height=720)
    fake_yt.add_video("both0000001", channel=CH)
    fake_yt.set_uploads(CH, ["both0000001"])
    fake_yt.set_likes(["both0000001"])
    sync_channel(h.user_id, CH, "manual")
    assert jobs_map() == {"both0000001": (jobs.PRIORITY_BACKFILL, 720)}
    run = sync_likes(h.user_id, trigger="manual")
    assert run.jobs_queued == 0  # merged into the existing job
    assert jobs_map() == {"both0000001": (jobs.PRIORITY_LIKE, 2160)}  # like raises cap + priority
    with read_session() as s:
        assert len(s.scalars(select(DownloadJob)).all()) == 1
        assert len(s.scalars(select(VideoSource)).all()) == 2


def test_private_deleted_placeholders_are_skipped(client, fake_yt):
    h = login(client)
    make_rule(h.user_id)
    fake_yt.add_video("good0000001", channel=CH)
    fake_yt.placeholders = {"priv0000001": "Private video", "del00000001": "Deleted video"}
    fake_yt.set_uploads(CH, ["good0000001", "priv0000001", "del00000001"])
    run = sync_channel(h.user_id, CH, "manual")
    assert run.jobs_queued == 1 and set(jobs_map()) == {"good0000001"} and run.items_seen == 3
    with read_session() as s:
        assert s.get(Video, "priv0000001") is None


def test_video_missing_from_videos_list_is_unavailable(client, fake_yt):
    h = login(client)
    make_rule(h.user_id)
    fake_yt.set_uploads(CH, ["gone0000001", "good0000001"])
    del fake_yt.videos["gone0000001"]  # API omits private/deleted videos
    run = sync_channel(h.user_id, CH, "manual")
    assert set(jobs_map()) == {"good0000001"} and run.status == SyncStatus.ok


def test_paused_or_off_rule_makes_no_calls(client, fake_yt):
    h = login(client)
    make_rule(h.user_id, paused=True)
    assert sync_channel(h.user_id, CH, "schedule").status == SyncStatus.ok
    assert sync_channel(h.user_id, "UCnotruled", "schedule").status == SyncStatus.ok
    assert fake_yt.calls == []


def test_playlist_404_is_recorded_as_error(client, fake_yt):
    h = login(client)
    make_rule(h.user_id)
    run = sync_channel(h.user_id, CH, "manual")
    assert run.status == SyncStatus.error and "playlistNotFound" in run.error
    assert rule(h.user_id)["status"] == SyncStatus.error


def test_refresh_stale_metadata(client, fake_yt):
    login(client)
    old = datetime.now(UTC) - timedelta(days=30)
    with write_session() as s:
        s.add(Channel(id="UCx", title="X", metadata_fetched_at=old))
        s.add(Video(id="stale000001", channel_id="UCx", title="old title", metadata_fetched_at=old))
        s.add(Video(id="gone0000001", channel_id="UCx", metadata_fetched_at=old))
        s.add(Video(id="fresh000001", channel_id="UCx", metadata_fetched_at=datetime.now(UTC)))
    fake_yt.add_video("stale000001", channel="UCx", title="new title")
    fake_yt.add_channel("UCx", title="X renamed")
    assert refresh_stale_metadata(25) == 3
    with read_session() as s:
        assert s.get(Video, "stale000001").title == "new title"
        assert s.get(Video, "gone0000001").availability == Availability.deleted  # record kept
        assert s.get(Channel, "UCx").title == "X renamed"
        assert s.get(Video, "fresh000001").metadata_fetched_at is not None
    assert refresh_stale_metadata(25) == 0
