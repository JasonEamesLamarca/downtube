"""0.5.0: delete an unsubscribed channel from the Subscriptions list; resubscribing reconciles."""

from __future__ import annotations

from helpers import _reset_in_memory_state, fake_yt, login  # noqa: F401
from sqlalchemy import select
from test_api import seed_channel, seed_video

from downtube.db import read_session, write_session
from downtube.models import (
    ArchiveMode,
    AuditEvent,
    ChannelRule,
    DiscoverySource,
    DownloadJob,
    JobStatus,
    MediaFile,
    Subscription,
    VideoSource,
)
from downtube.sync.subscriptions import sync_subscriptions


def _unsubscribed_channel_with_archive(h, env, *, mode=ArchiveMode.backfill_and_follow):
    seed_channel(h.user_id, mode=mode)
    seed_video(h.user_id, "kept0000001", DiscoverySource.subscription, archived_file=(env["archive"], "Alpha/a.mp4"))
    seed_video(h.user_id, "queued00001", DiscoverySource.subscription, job=JobStatus.queued)
    with write_session() as s:
        rid = s.scalar(select(ChannelRule.id))
        for src in s.scalars(select(VideoSource)):
            src.channel_rule_id = rid
        s.scalar(select(Subscription)).active = False  # unsubscribed (here or directly on YouTube)


def test_delete_requires_unsubscribed(client):
    h = login(client)
    seed_channel(h.user_id)
    r = client.post("/api/subscriptions/UCa/delete", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "still_subscribed"
    assert client.post("/api/subscriptions/UCzz/delete", headers=h).status_code == 404


def test_delete_keeps_videos_and_turns_rule_off(client, env):
    h = login(client)
    _unsubscribed_channel_with_archive(h, env)
    r = client.post("/api/subscriptions/UCa/delete", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"channel_id": "UCa", "title": "Alpha", "archived_kept": 1, "jobs_cancelled": 1}

    assert client.get("/api/subscriptions", headers=h).json()["total"] == 0
    lib = client.get("/api/library", headers=h).json()
    assert [i["video_id"] for i in lib["items"]] == ["kept0000001"]
    with read_session() as s:
        assert s.scalar(select(Subscription)) is None
        assert s.scalar(select(ChannelRule)).mode == ArchiveMode.off  # rule kept (settings), but off
        assert s.scalar(select(DownloadJob)).status == JobStatus.cancelled
        assert s.scalar(select(MediaFile)).is_current is True
        assert len(list(s.scalars(select(VideoSource)))) == 2
        assert s.scalar(select(AuditEvent).where(AuditEvent.action == "subscription.delete")) is not None


def test_resubscribe_brings_channel_back_with_existing_archive(client, env, fake_yt):
    h = login(client)
    _unsubscribed_channel_with_archive(h, env, mode=ArchiveMode.off)
    assert client.post("/api/subscriptions/UCa/delete", headers=h).status_code == 200

    # A sync that does not list the channel does not bring it back ...
    fake_yt.add_subscription_page("UCb")
    sync_subscriptions(h.user_id, "manual")
    assert {i["channel_id"] for i in client.get("/api/subscriptions", headers=h).json()["items"]} == {"UCb"}

    # ... subscribing again on YouTube does, and the archived video is counted again.
    fake_yt.subscriptions = []
    fake_yt.add_channel("UCa", title="Alpha")
    fake_yt.add_subscription_page("UCa", "UCb")
    sync_subscriptions(h.user_id, "manual")
    items = {i["channel_id"]: i for i in client.get("/api/subscriptions", headers=h).json()["items"]}
    assert items["UCa"]["subscribed"] is True and items["UCa"]["archived_count"] == 1
    assert items["UCa"]["rule"]["mode"] == "off"


def test_subscribed_filter(client):
    h = login(client)
    seed_channel(h.user_id, "UCa", "Alpha")
    seed_channel(h.user_id, "UCb", "Beta")
    with write_session() as s:
        s.scalar(select(Subscription).where(Subscription.channel_id == "UCb")).active = False
    ids = lambda q: [i["channel_id"] for i in client.get(f"/api/subscriptions{q}", headers=h).json()["items"]]  # noqa: E731
    assert ids("") == ["UCa", "UCb"]
    assert ids("?subscribed=false") == ["UCb"]
    assert ids("?subscribed=true") == ["UCa"]
