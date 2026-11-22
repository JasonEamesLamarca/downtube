"""0.3.0: delete from the library, unsubscribe, manage permission, description (.nfo) files."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from helpers import _reset_in_memory_state, fake_yt, login  # noqa: F401
from sqlalchemy import select
from test_api import seed_channel, seed_video

from downtube.auth import oauth
from downtube.db import read_session, write_session
from downtube.downloads import jobs
from downtube.downloads.manager import nfo_xml
from downtube.models import (
    ArchiveMode,
    AuditEvent,
    ChannelRule,
    DiscoverySource,
    DownloadJob,
    GoogleAccount,
    JobStatus,
    MediaFile,
    Subscription,
    Video,
    VideoExclusion,
    VideoSource,
)
from downtube.youtube import quota

MANAGE = f"{oauth.YOUTUBE_SCOPE} {oauth.MANAGE_SCOPE}"


def grant_manage(account_id: int, scopes: str = MANAGE) -> None:
    with write_session() as s:
        s.get(GoogleAccount, account_id).scopes = scopes


def archived(env, h, vid: str, source=DiscoverySource.like, *, sidecars=True, kept=None) -> Path:
    folder = env["archive"] / "Alpha"
    folder.mkdir(exist_ok=True)
    f = folder / f"2026-01-01 - T [{vid}].mp4"
    f.write_bytes(b"video")
    if sidecars:
        f.with_suffix(".jpg").write_bytes(b"jpg")
        f.with_suffix(".nfo").write_text("<movie/>")
    seed_video(h.user_id, vid, source, archived_file=(env["archive"], f"Alpha/{f.name}"))
    if kept:
        old = folder / kept
        old.write_bytes(b"old")
        with write_session() as s:
            s.add(
                MediaFile(
                    video_id=vid,
                    root_path=str(env["archive"]),
                    rel_path=f"Alpha/{f.name}",
                    container="mp4",
                    width=1280,
                    height=720,
                    size_bytes=3,
                    is_current=False,
                    superseded_action=f"kept:{kept}",
                )
            )
    return f


# --------------------------------------------------------------------------- permission


def test_manage_login_url_and_me_flag(client):
    h = login(client)
    r = client.get("/api/auth/login?manage=true", follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert oauth.MANAGE_SCOPE in q["scope"][0].split() and q["prompt"] == ["consent"]
    plain = parse_qs(urlparse(client.get("/api/auth/login", follow_redirects=False).headers["location"]).query)
    assert oauth.MANAGE_SCOPE not in plain["scope"][0]
    assert client.get("/api/auth/me", headers=h).json()["can_manage_youtube"] is False
    grant_manage(h.account_id)
    assert client.get("/api/auth/me", headers=h).json()["can_manage_youtube"] is True
    assert oauth.has_manage_scope("https://www.googleapis.com/auth/youtube") is True
    assert oauth.has_manage_scope(oauth.YOUTUBE_SCOPE) is False


# --------------------------------------------------------------------------- delete


def test_delete_liked_video_removes_like_files_and_excludes(client, env, fake_yt):
    h = login(client)
    grant_manage(h.account_id)
    f = archived(env, h, "del00000001", kept="2026-01-01 - T [del00000001].superseded.mp4")
    r = client.post("/api/library/del00000001/delete", json={"remove_like": True}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["like_removed"] is True and body["excluded"] is True
    assert fake_yt.ratings == {"del00000001": "none"}
    assert sorted(Path(p).suffix for p in body["deleted_files"]) == [".jpg", ".mp4", ".mp4", ".nfo"]
    assert list(f.parent.iterdir()) == []
    assert client.get("/api/library", headers=h).json()["total"] == 0
    with read_session() as s:
        assert s.get(VideoExclusion, (h.user_id, "del00000001")) is not None
        assert s.scalar(select(VideoSource)).active is False
        assert all(not m.is_current for m in s.scalars(select(MediaFile)))
        assert s.scalar(select(AuditEvent).where(AuditEvent.action == "library.delete")) is not None
    assert quota.usage_today()[0] == 50
    liked = client.get("/api/likes", headers=h).json()["items"]
    assert liked == [] or liked[0]["archive_state"] == "excluded"


def test_delete_liked_without_permission_changes_nothing(client, env, fake_yt):
    h = login(client)
    f = archived(env, h, "del00000002")
    r = client.post("/api/library/del00000002/delete", json={"remove_like": True}, headers=h)
    assert r.status_code == 403 and r.json()["error"]["code"] == "manage_permission_required"
    assert f.exists() and fake_yt.ratings == {}
    # keeping the like works without the permission
    r = client.post("/api/library/del00000002/delete", json={"remove_like": False}, headers=h)
    assert r.status_code == 200 and r.json()["like_removed"] is False
    assert "still liked" in r.json()["warnings"][0]
    assert not f.exists() and fake_yt.calls == []


def test_deleted_subscription_video_is_never_requeued_until_requested(client, env, fake_yt):
    h = login(client)
    seed_channel(h.user_id, mode=ArchiveMode.backfill_and_follow)
    archived(env, h, "del00000003", DiscoverySource.subscription)
    r = client.post("/api/library/del00000003/delete", json={}, headers=h)
    assert r.status_code == 200 and r.json()["like_removed"] is False and fake_yt.calls == []
    with write_session() as s:
        assert jobs.compute_effective_cap(s, "del00000003") is None
        assert jobs.enqueue(s, "del00000003", user_id=h.user_id) is None
    # an explicit manual download lifts the exclusion
    fake_yt.add_video("del00000003", channel="UCa")
    r = client.post("/api/downloads", json={"video_id": "del00000003"}, headers=h)
    assert r.status_code == 200, r.text
    with read_session() as s:
        assert s.get(VideoExclusion, (h.user_id, "del00000003")) is None


def test_delete_refuses_shared_and_running(client, env):
    h = login(client)
    archived(env, h, "del00000004", DiscoverySource.subscription)
    other = login(client, "other@example.com")
    with write_session() as s:
        s.add(VideoSource(user_id=other.user_id, video_id="del00000004", source=DiscoverySource.like))
    r = client.post("/api/library/del00000004/delete", json={}, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "shared_video"

    archived(env, h, "del00000005", DiscoverySource.subscription)
    with write_session() as s:
        s.add(DownloadJob(video_id="del00000005", user_id=h.user_id, status=JobStatus.running))
    r = client.post("/api/library/del00000005/delete", json={}, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "job_running"
    assert client.post("/api/library/unknown0001/delete", json={}, headers=h).status_code == 404


# --------------------------------------------------------------------------- unsubscribe


def test_unsubscribe(client, fake_yt):
    h = login(client)
    seed_channel(h.user_id, mode=ArchiveMode.backfill_and_follow)
    with write_session() as s:
        rid = s.scalar(select(ChannelRule.id))
    seed_video(h.user_id, "uns00000001", DiscoverySource.subscription, job=JobStatus.queued)
    with write_session() as s:
        s.scalar(select(VideoSource)).channel_rule_id = rid

    r = client.post("/api/subscriptions/UCa/unsubscribe", headers=h)
    assert r.status_code == 403 and r.json()["error"]["code"] == "manage_permission_required"

    grant_manage(h.account_id)
    fake_yt.subscription_ids["UCa"] = "sub-123"
    r = client.post("/api/subscriptions/UCa/unsubscribe", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["subscribed"] is False and r.json()["rule"]["mode"] == "off"
    assert fake_yt.deleted_subscriptions == ["sub-123"]
    with read_session() as s:
        assert s.scalar(select(Subscription)).active is False
        assert s.scalar(select(DownloadJob)).status == JobStatus.cancelled
    assert quota.usage_today()[0] == 51  # lookup (1) + delete (50)
    r = client.post("/api/subscriptions/UCa/unsubscribe", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_subscribed"


def test_unsubscribe_when_already_gone_on_youtube(client, fake_yt):
    h = login(client)
    grant_manage(h.account_id)
    seed_channel(h.user_id)
    r = client.post("/api/subscriptions/UCa/unsubscribe", headers=h)
    assert r.status_code == 200 and fake_yt.deleted_subscriptions == []


def test_youtube_permission_error_is_mapped(client, fake_yt):
    import httpx

    h = login(client)
    grant_manage(h.account_id)
    seed_channel(h.user_id)
    fake_yt.fail = [
        httpx.Response(403, json={"error": {"errors": [{"reason": "insufficientPermissions"}], "message": "x"}})
    ]
    r = client.post("/api/subscriptions/UCa/unsubscribe", headers=h)
    assert r.status_code == 403 and r.json()["error"]["code"] == "manage_permission_required"
    with read_session() as s:
        assert s.scalar(select(Subscription)).active is True


# --------------------------------------------------------------------------- description files


def test_nfo_contains_description_and_is_valid_xml():
    import xml.etree.ElementTree as ET

    v = Video(
        id="abcdefghijk",
        title="A <b> & c",
        description="Line 1\nLine 2 \x0b with control char",
        published_at=datetime(2026, 3, 4, tzinfo=UTC),
        duration_s=125,
    )
    root = ET.fromstring(nfo_xml(v, "Chan & Co"))
    assert root.findtext("title") == "A <b> & c"
    assert root.findtext("plot") == "Line 1\nLine 2  with control char"
    assert root.findtext("year") == "2026" and root.findtext("premiered") == "2026-03-04"
    assert root.findtext("runtime") == "2" and root.findtext("studio") == "Chan & Co"
    assert root.find("uniqueid").text == "abcdefghijk"


def test_sidecar_backfill_writes_missing_nfo(client, env):
    h = login(client)
    f1 = archived(env, h, "nfo00000001", sidecars=False)
    f2 = archived(env, h, "nfo00000002")  # already has a .nfo
    with write_session() as s:
        s.get(Video, "nfo00000001").description = "The description"
    r = client.post("/api/library/sidecars", headers=h)
    assert r.json() == {"written": 1, "skipped": 1, "failed": 0}
    assert "<plot>The description</plot>" in f1.with_suffix(".nfo").read_text()
    assert f2.with_suffix(".nfo").read_text() == "<movie/>"
    r = client.post("/api/library/sidecars?overwrite=true", headers=h)
    assert r.json()["written"] == 2


def test_new_defaults_write_nfo_and_embed_metadata(client):
    h = login(client)
    st = client.get("/api/settings", headers=h).json()
    assert st["write_nfo"] is True and st["embed_metadata"] is True


# --------------------------------------------------------------------------- library channel filter


def test_library_channels_lists_every_archived_channel(client, env):
    from downtube.models import Channel

    h = login(client)
    other = login(client, "second@example.com")
    with write_session() as s:
        for i in range(120):  # more videos than the old 100-item sample
            s.add(Channel(id=f"UC{i:03d}", title=f"Chan {i:03d}"))
    for i in range(120):
        seed_video(h.user_id, f"lib{i:08d}", channel=f"UC{i:03d}", archived_file=(env["archive"], f"x/{i}.mp4"))
    seed_video(h.user_id, "lib99999998", channel="UC000", archived_file=(env["archive"], "x/a.mp4"))
    seed_video(h.user_id, "nofile00001", channel="UC119")  # not archived: no extra count
    seed_video(other.user_id, "oth00000001", channel="UCzz", archived_file=(env["archive"], "x/o.mp4"))
    with write_session() as s:
        s.get(Channel, "UC005").title = "alpha lower-case"
    r = client.get("/api/library/channels", headers=h)
    assert r.status_code == 200, r.text
    chans = r.json()
    assert len(chans) == 120
    assert chans[0] == {"channel_id": "UC005", "title": "alpha lower-case", "count": 1}
    assert {c["channel_id"]: c["count"] for c in chans}["UC000"] == 2
    assert "UCzz" not in {c["channel_id"] for c in chans}  # other users' archives stay private


def test_vertical_videos_use_short_side_for_height(client, env):
    from downtube.models import MediaFile

    h = login(client)
    seed_video(h.user_id, "vert0000001", archived_file=(env["archive"], "x/v.mp4"))
    seed_video(h.user_id, "wide0000001", archived_file=(env["archive"], "x/w.mp4"))
    with write_session() as s:
        mf = s.scalar(select(MediaFile).where(MediaFile.video_id == "vert0000001"))
        mf.width, mf.height = 1080, 1920
        mf2 = s.scalar(select(MediaFile).where(MediaFile.video_id == "wide0000001"))
        mf2.width, mf2.height = 2560, 1440
    likes = {v["video_id"]: v["archived_height"] for v in client.get("/api/likes", headers=h).json()["items"]}
    assert likes == {"vert0000001": 1080, "wide0000001": 1440}
    order = client.get("/api/library?sort=height&order=desc", headers=h).json()["items"]
    assert [m["video_id"] for m in order] == ["wide0000001", "vert0000001"]
