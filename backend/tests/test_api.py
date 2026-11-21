from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
import respx
from helpers import _reset_in_memory_state, fake_yt, login, vid_id  # noqa: F401
from sqlalchemy import select

from downtube.db import read_session, write_session
from downtube.models import (
    ArchiveMode,
    AuditEvent,
    Channel,
    ChannelRule,
    DiscoverySource,
    DownloadJob,
    GoogleAccount,
    JobStatus,
    MediaFile,
    Subscription,
    SyncRun,
    SyncStatus,
    User,
    Video,
    VideoSource,
    WebSession,
)

MUTATIONS = [
    ("post", "/api/auth/acknowledge"),
    ("put", "/api/settings"),
    ("post", "/api/subscriptions/sync"),
    ("post", "/api/downloads/1/cancel"),
]


def seed_channel(user_id, cid="UCa", title="Alpha", count=10, mode=None, **kw):
    with write_session() as s:
        if s.get(Channel, cid) is None:
            s.add(Channel(id=cid, title=title, video_count=count, uploads_playlist_id="UU" + cid[2:]))
            s.flush()
        s.add(Subscription(user_id=user_id, channel_id=cid))
        if mode:
            s.add(ChannelRule(user_id=user_id, channel_id=cid, mode=mode, activated_at=datetime.now(UTC), **kw))


def seed_video(user_id, vid, source=DiscoverySource.like, *, channel="UCa", archived_file=None, job=None, **kw):
    with write_session() as s:
        if s.get(Channel, channel) is None:
            s.add(Channel(id=channel, title=channel))
            s.flush()
        s.add(Video(id=vid, channel_id=channel, title=f"T {vid}", availability="public", **kw))
        s.flush()
        s.add(VideoSource(user_id=user_id, video_id=vid, source=source))
        if archived_file:
            root, rel = archived_file
            s.add(
                MediaFile(
                    video_id=vid,
                    root_path=str(root),
                    rel_path=rel,
                    container="mp4",
                    width=1920,
                    height=1080,
                    size_bytes=5000,
                    best_available_height=1080,
                )
            )
        if job:
            s.flush()
            s.add(DownloadJob(video_id=vid, user_id=user_id, status=job))


# --------------------------------------------------------------------------- auth / csrf


def test_unauthenticated_requests_get_401_json(client):
    for path in (
        "/api/auth/me",
        "/api/dashboard",
        "/api/subscriptions",
        "/api/likes",
        "/api/downloads",
        "/api/library",
        "/api/settings",
        "/api/storage",
        "/api/quota",
        "/api/sync/runs",
        "/api/logs",
    ):
        r = client.get(path)
        assert r.status_code == 401, path
        assert r.json() == {"error": {"code": "unauthenticated", "message": "authentication required"}}
    assert client.get("/api/health").status_code == 200  # no auth needed


def test_csrf_required_on_mutations_only(client):
    h = login(client)
    cookie_only = {"Cookie": h["Cookie"]}
    assert client.get("/api/auth/me", headers=cookie_only).status_code == 200  # GET needs no CSRF
    for method, path in MUTATIONS:
        r = getattr(client, method)(path, headers=cookie_only)
        assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed", path
        bad = getattr(client, method)(path, headers={**cookie_only, "X-CSRF-Token": "x" * 32})
        assert bad.status_code == 403 and bad.json()["error"]["code"] == "csrf_failed"
    ok = client.post("/api/auth/acknowledge", headers=h)
    assert ok.status_code == 200 and ok.json()["acknowledged_terms"] is True


def test_csrf_token_is_per_session_and_me_exposes_it(client):
    a1, a2 = login(client), login(client)
    assert a1.csrf != a2.csrf
    assert client.get("/api/auth/me", headers=a1).json()["csrf_token"] == a1.csrf
    assert (
        client.post("/api/auth/acknowledge", headers={"Cookie": a1["Cookie"], "X-CSRF-Token": a2.csrf}).status_code
        == 403
    )


def test_expired_session_is_rejected(client):
    h = login(client)
    with write_session() as s:
        for w in s.scalars(select(WebSession)):
            w.expires_at = datetime(2020, 1, 1, tzinfo=UTC)
    assert client.get("/api/auth/me", headers=h).status_code == 401


# --------------------------------------------------------------------------- acknowledgement gate


def test_acknowledgement_gate(client):
    h = login(client, acknowledged=False)
    seed_channel(h.user_id, mode=None)
    ack = lambda r: r.status_code == 403 and r.json()["error"]["code"] == "acknowledgement_required"  # noqa: E731
    assert ack(client.patch("/api/subscriptions/UCa/rule", json={"mode": "follow_new"}, headers=h))
    assert ack(
        client.post(
            "/api/subscriptions/bulk", json={"channel_ids": ["UCa"], "patch": {"mode": "backfill_once"}}, headers=h
        )
    )
    assert ack(client.post("/api/downloads", json={"video_id": "abcdefghijk"}, headers=h))
    assert ack(client.post("/api/likes/backfill", json={}, headers=h))
    assert ack(client.put("/api/settings", json={"likes_mode": "all"}, headers=h))
    assert ack(client.post("/api/subscriptions/UCa/sync", headers=h))
    # not gated: turning things off / editing filters / reading
    assert (
        client.patch(
            "/api/subscriptions/UCa/rule", json={"mode": "off", "include_shorts": False}, headers=h
        ).status_code
        == 200
    )
    assert client.put("/api/settings", json={"likes_mode": "off", "min_free_gb": 5}, headers=h).status_code == 200
    # acknowledging unlocks
    client.post("/api/auth/acknowledge", headers=h)
    r = client.patch("/api/subscriptions/UCa/rule", json={"mode": "follow_new"}, headers=h)
    assert r.status_code == 200 and r.json()["rule"]["mode"] == "follow_new" and r.json()["rule"]["activated_at"]


# --------------------------------------------------------------------------- isolation


def test_account_isolation(client, tmp_path, env):
    a, b = login(client, "owner@example.com"), login(client, "second@example.com")
    f = env["archive"] / "Alpha" / "v.mp4"
    f.parent.mkdir()
    f.write_bytes(b"x" * 100)
    seed_channel(a.user_id, mode=ArchiveMode.follow_new)
    seed_video(a.user_id, "likedvideo01", archived_file=(env["archive"], "Alpha/v.mp4"))
    seed_video(a.user_id, "queuedvid01", job=JobStatus.queued)
    with write_session() as s:
        s.add(SyncRun(user_id=a.user_id, kind="likes", status=SyncStatus.ok))
        jid = s.scalar(select(DownloadJob.id))
    for path in ("/api/subscriptions", "/api/likes", "/api/library", "/api/downloads", "/api/sync/runs"):
        mine, theirs = client.get(path, headers=a).json(), client.get(path, headers=b).json()
        assert mine["total"] >= 1, path
        assert theirs["total"] == 0 and theirs["items"] == [], path
    assert client.get("/api/downloads/counts", headers=b).json()["queued"] == 0
    assert client.get("/api/downloads/counts", headers=a).json()["queued"] == 1
    d = client.get("/api/dashboard", headers=b).json()
    assert d["archived_videos"] == 0 and d["liked_videos_discovered"] == 0 and d["monitored_channels"] == 0
    assert client.get("/api/library/likedvideo01/stream", headers=a).status_code == 200
    assert client.get("/api/library/likedvideo01/stream", headers=b).status_code == 404
    assert client.get("/api/subscriptions/UCa/estimate", headers=b).status_code == 404
    assert client.patch("/api/subscriptions/UCa/rule", json={"mode": "off"}, headers=b).status_code == 404
    assert client.post(f"/api/downloads/{jid}/cancel", headers=b).status_code == 404
    assert client.post("/api/subscriptions/UCa/sync", headers=b).status_code == 404
    bulk = client.post(
        "/api/subscriptions/bulk", json={"channel_ids": ["UCa"], "patch": {"mode": "off"}}, headers=b
    ).json()
    assert bulk["updated"] == [] and bulk["errors"] == {"UCa": "not subscribed"}
    assert client.post(f"/api/downloads/{jid}/cancel", headers=a).json()["status"] == "cancelled"


# --------------------------------------------------------------------------- subscriptions / rules


def test_subscription_listing_filters_sort_and_counts(client, env):
    h = login(client)
    seed_channel(h.user_id, "UCa", "Alpha", 5, mode=ArchiveMode.follow_new)
    seed_channel(h.user_id, "UCb", "beta_x", 50)
    seed_channel(h.user_id, "UCc", "Gamma", 500)
    seed_video(
        h.user_id, "archived001", DiscoverySource.subscription, channel="UCb", archived_file=(env["archive"], "a.mp4")
    )
    seed_video(h.user_id, "queued00001", DiscoverySource.subscription, channel="UCb", job=JobStatus.queued)
    seed_video(h.user_id, "failed00001", DiscoverySource.subscription, channel="UCb", job=JobStatus.failed)
    items = client.get("/api/subscriptions", headers=h).json()["items"]
    assert [i["title"] for i in items] == ["Alpha", "beta_x", "Gamma"]  # case-insensitive title sort
    b = items[1]
    assert (b["archived_count"], b["queued_count"], b["failed_count"], b["downloading_count"]) == (1, 1, 1, 0)
    assert b["url"] == "https://www.youtube.com/channel/UCb" and b["rule"]["mode"] == "off"
    assert [
        i["channel_id"] for i in client.get("/api/subscriptions?sort=video_count&order=desc", headers=h).json()["items"]
    ] == ["UCc", "UCb", "UCa"]
    assert [
        i["channel_id"] for i in client.get("/api/subscriptions?sort=archived&order=desc", headers=h).json()["items"]
    ][0] == "UCb"
    assert [i["channel_id"] for i in client.get("/api/subscriptions?mode=follow_new", headers=h).json()["items"]] == [
        "UCa"
    ]
    assert len(client.get("/api/subscriptions?mode=off", headers=h).json()["items"]) == 2
    assert [i["channel_id"] for i in client.get("/api/subscriptions?q=beta_", headers=h).json()["items"]] == ["UCb"]
    assert client.get("/api/subscriptions?q=%25", headers=h).json()["total"] == 0  # LIKE wildcards are escaped
    page = client.get("/api/subscriptions?page=2&page_size=2", headers=h).json()
    assert page["total"] == 3 and len(page["items"]) == 1 and page["page"] == 2
    assert client.get("/api/subscriptions?sort=bogus", headers=h).status_code == 422


def test_large_channel_requires_confirmation(client):
    h = login(client)
    seed_channel(h.user_id, "UCbig", "Big", 500)
    r = client.patch("/api/subscriptions/UCbig/rule", json={"mode": "backfill_once"}, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "confirmation_required"
    est = r.json()["error"]["details"]["estimate"]
    assert est["video_count"] == 500 and est["requires_confirmation"] is True and est["estimate_basis"]
    with read_session() as s:  # nothing was changed
        assert s.scalar(select(ChannelRule)) is None
    # follow_new never needs confirmation; the estimate endpoint is available
    assert client.get("/api/subscriptions/UCbig/estimate", headers=h).json()["is_large"] is True
    ok = client.patch(
        "/api/subscriptions/UCbig/rule",
        json={"mode": "backfill_once", "confirm_large": True, "max_height": 720},
        headers=h,
    )
    assert ok.status_code == 200 and ok.json()["rule"]["max_height"] == 720
    cleared = client.patch("/api/subscriptions/UCbig/rule", json={"clear_max_height": True}, headers=h).json()
    assert cleared["rule"]["max_height"] is None and cleared["rule"]["mode"] == "backfill_once"
    seed_channel(h.user_id, "UCbig2", "Big2", 500)
    assert client.patch("/api/subscriptions/UCbig2/rule", json={"mode": "follow_new"}, headers=h).status_code == 200
    bulk = client.post(
        "/api/subscriptions/bulk",
        json={"channel_ids": ["UCbig2", "UCnope"], "patch": {"mode": "backfill_and_follow"}},
        headers=h,
    ).json()
    assert [e["channel_id"] for e in bulk["needs_confirmation"]] == ["UCbig2"] and bulk["errors"] == {
        "UCnope": "not subscribed"
    }


def test_rule_off_deactivates_sources_and_cancels_queued_jobs(client):
    h = login(client)
    seed_channel(h.user_id, mode=ArchiveMode.backfill_and_follow)
    with write_session() as s:
        rid = s.scalar(select(ChannelRule.id))
    seed_video(h.user_id, "chvideo0001", DiscoverySource.subscription, job=JobStatus.queued)
    with write_session() as s:
        s.scalar(select(VideoSource)).channel_rule_id = rid
    r = client.patch("/api/subscriptions/UCa/rule", json={"paused": True}, headers=h)
    assert r.status_code == 200 and r.json()["rule"]["paused"] is True
    with read_session() as s:
        assert s.scalar(select(VideoSource)).active is False
        assert s.scalar(select(DownloadJob)).status == JobStatus.cancelled
    # resuming restarts the backfill from the beginning
    r = client.patch("/api/subscriptions/UCa/rule", json={"paused": False}, headers=h)
    assert r.json()["rule"]["backfill_completed_at"] is None


def test_manual_sync_returns_202_with_run(client, fake_yt):
    h = login(client)
    fake_yt.add_subscription_page("UCa")
    r = client.post("/api/subscriptions/sync", headers=h)
    assert r.status_code == 202
    body = r.json()
    assert body["kind"] == "subscriptions" and body["trigger"] == "manual" and body["status"] in ("ok", "running")
    runs = client.get("/api/sync/runs?kind=subscriptions", headers=h).json()
    assert runs["total"] == 1 and runs["items"][0]["items_seen"] == 1
    fake_yt.set_likes([vid_id(1)])
    r = client.post("/api/likes/sync", json={"full": True}, headers=h)
    assert r.status_code == 202 and r.json()["kind"] == "likes_full"
    assert client.post("/api/likes/sync", headers=h).json()["kind"] == "likes"


def test_manual_sync_guard_returns_running_run(client):
    from downtube.sync import runner

    h = login(client)
    with write_session() as s:
        s.add(SyncRun(user_id=h.user_id, kind="subscriptions", status=SyncStatus.running))
        rid = s.scalar(select(SyncRun.id))
    runner._active.add(("subscriptions", h.user_id, ""))
    r = client.post("/api/subscriptions/sync", headers=h)
    assert r.status_code == 202 and r.json()["id"] == rid and r.json()["status"] == "running"
    with read_session() as s:
        assert len(s.scalars(select(SyncRun)).all()) == 1  # no second run started


# --------------------------------------------------------------------------- likes / downloads


def test_likes_listing_state_filter_and_backfill(client, fake_yt):
    h = login(client)
    seed_video(h.user_id, "liked000001")
    seed_video(h.user_id, "liked000002", job=JobStatus.queued)
    with write_session() as s:  # baseline like (future_only): known but not archivable
        for src in s.scalars(select(VideoSource)):
            src.archive_eligible = src.video_id == "liked000002"
    res = client.get("/api/likes", headers=h).json()
    assert res["total"] == 2
    st = {i["video_id"]: i["archive_state"] for i in res["items"]}
    assert st == {"liked000001": "excluded", "liked000002": "queued"}
    assert client.get("/api/likes?state=queued", headers=h).json()["total"] == 1
    assert client.get("/api/likes?q=liked000001", headers=h).json()["total"] == 1
    assert client.get("/api/likes?state=bogus", headers=h).status_code == 422
    r = client.post("/api/likes/backfill", json={"confirm": False}, headers=h)
    assert r.status_code == 200 and r.json() == {"queued": 1}
    st = {i["video_id"]: i["archive_state"] for i in client.get("/api/likes", headers=h).json()["items"]}
    assert st["liked000001"] == "queued"
    with read_session() as s:
        assert {j.priority for j in s.scalars(select(DownloadJob))} == {50, 80}  # seeded job + backfill
    client.put("/api/settings", json={"likes_mode": "off"}, headers=h)
    assert client.post("/api/likes/backfill", json={}, headers=h).json()["error"]["code"] == "likes_disabled"


def test_download_actions_and_state_errors(client):
    h = login(client)
    seed_video(h.user_id, "vidqueued01", job=JobStatus.queued)
    jid = client.get("/api/downloads", headers=h).json()["items"][0]["id"]
    assert client.post(f"/api/downloads/{jid}/pause", headers=h).json()["status"] == "paused"
    assert client.post(f"/api/downloads/{jid}/pause", headers=h).status_code == 409
    assert client.post(f"/api/downloads/{jid}/resume", headers=h).json()["status"] == "queued"
    assert client.post(f"/api/downloads/{jid}/retry", headers=h).json()["error"]["code"] == "invalid_state"
    assert client.post(f"/api/downloads/{jid}/cancel", headers=h).json()["status"] == "cancelled"
    assert client.post(f"/api/downloads/{jid}/retry", headers=h).json()["status"] == "queued"
    assert client.post("/api/downloads/9999/cancel", headers=h).status_code == 404
    assert client.get("/api/downloads?status=queued", headers=h).json()["total"] == 1
    assert client.get("/api/downloads?status=failed", headers=h).json()["total"] == 0
    c = client.get("/api/downloads/counts", headers=h).json()
    assert c["queued"] == 1 and c["completed"] == 0 and set(c) >= {"queued", "running", "paused", "skipped"}


def test_manual_enqueue_fetches_metadata_and_dedupes(client, fake_yt):
    h = login(client)
    fake_yt.add_video("manual00001", title="Manual one")
    r = client.post("/api/downloads", json={"video_id": "manual00001", "max_height": 720}, headers=h)
    assert r.status_code == 200
    job = r.json()
    assert (
        job["title"] == "Manual one"
        and job["max_height"] == 720
        and job["priority"] == 20
        and job["status"] == "queued"
    )
    again = client.post("/api/downloads", json={"video_id": "manual00001"}, headers=h).json()
    assert again["id"] == job["id"]
    assert fake_yt.count("videos") == 1  # metadata fetched once
    assert (
        client.post("/api/downloads", json={"video_id": "missing0001"}, headers=h).json()["error"]["code"]
        == "video_not_found"
    )
    assert client.post("/api/downloads", json={"video_id": "bad"}, headers=h).status_code == 422


# --------------------------------------------------------------------------- settings / storage


def test_settings_get_put_validation_and_reschedule(client, monkeypatch):
    from downtube import scheduler

    h = login(client)
    calls = []
    monkeypatch.setattr(scheduler, "reschedule", lambda: calls.append(1))
    s = client.get("/api/settings", headers=h).json()
    assert s["likes_mode"] == "future_only" and s["likes_poll_minutes"] == 15
    r = client.put(
        "/api/settings",
        json={"likes_poll_minutes": 30, "default_max_height": 1080, "download_window": "01:00-06:30"},
        headers=h,
    )
    assert r.status_code == 200 and r.json()["likes_poll_minutes"] == 30 and r.json()["default_max_height"] == 1080
    assert calls == [1]
    assert client.get("/api/settings", headers=h).json()["download_window"] == "01:00-06:30"
    for bad in (
        {"likes_poll_minutes": 1},
        {"max_concurrent_downloads": 99},
        {"default_max_height": 4320},
        {"likes_mode": "sometimes"},
        {"unknown_field": 1},
        {"min_free_gb": 0},
    ):
        r = client.put("/api/settings", json=bad, headers=h)
        assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error", bad
    for bad in ({"timezone": "Mars/Base"}, {"download_window": "25:00-26:00"}):
        r = client.put("/api/settings", json=bad, headers=h)
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_setting", bad
    assert client.put("/api/settings", json={"archive_root": "/etc"}, headers=h).status_code == 400
    assert client.get("/api/settings", headers=h).json()["likes_poll_minutes"] == 30  # failures changed nothing
    with read_session() as s:
        assert s.scalar(select(AuditEvent).where(AuditEvent.action == "settings.update")) is not None


def test_storage_browse_rejects_traversal_and_symlink_escape(client, env, tmp_path):
    h = login(client)
    (env["archive"] / "Movies").mkdir()
    os.symlink(env["data"], env["archive"] / "evil")
    ok = client.get("/api/storage/browse", headers=h).json()
    assert ok["path"] == str(env["archive"].resolve()) and [e["name"] for e in ok["entries"]] == ["Movies"]
    for bad in (
        "/etc",
        "/",
        f"{env['archive']}/../data",
        str(env["archive"] / "evil"),
        "relative/dir",
        f"{env['archive']}/Movies/../../data",
    ):
        r = client.get("/api/storage/browse", params={"path": bad}, headers=h)
        assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_path", bad
    assert (
        client.get("/api/storage/browse", params={"path": str(env["archive"] / "Movies")}, headers=h).status_code == 200
    )


def test_storage_info_and_set_root(client, env):
    h = login(client)
    info = client.get("/api/storage", headers=h).json()
    assert info["root_path"] == str(env["archive"]) and info["writable"] is True and info["total_bytes"] > 0
    assert client.post("/api/storage/root", json={"path": "/etc"}, headers=h).status_code == 400
    assert (
        client.post("/api/storage/root", json={"path": str(env["archive"] / "nope")}, headers=h).json()["error"]["code"]
        == "invalid_path"
    )
    r = client.post("/api/storage/root", json={"path": str(env["archive"] / "New"), "create": True}, headers=h)
    assert r.status_code == 200 and r.json()["root_path"] == str((env["archive"] / "New").resolve())
    assert (env["archive"] / "New").is_dir()
    assert client.get("/api/settings", headers=h).json()["archive_root"] == str((env["archive"] / "New").resolve())
    (env["archive"] / "ro").mkdir()
    (env["archive"] / "ro").chmod(0o500)
    try:
        if os.geteuid() != 0:
            r = client.post("/api/storage/root", json={"path": str(env["archive"] / "ro")}, headers=h)
            assert r.status_code == 400 and r.json()["error"]["code"] == "not_writable"
    finally:
        (env["archive"] / "ro").chmod(0o700)


# --------------------------------------------------------------------------- library


def test_library_listing_and_stream_with_range(client, env):
    h = login(client)
    f = env["archive"] / "Alpha" / "2026 - v [lib00000001].mp4"
    f.parent.mkdir()
    f.write_bytes(bytes(range(256)) * 4)
    f.with_suffix(".jpg").write_bytes(b"\xff\xd8jpg")
    seed_video(h.user_id, "lib00000001", archived_file=(env["archive"], f"Alpha/{f.name}"))
    lib = client.get("/api/library", headers=h).json()
    item = lib["items"][0]
    assert lib["total"] == 1 and item["height"] == 1080 and item["sources"] == ["like"]
    assert item["thumbnail_url"] == "/api/library/lib00000001/thumbnail"
    assert client.get("/api/library/lib00000001/thumbnail", headers=h).content == b"\xff\xd8jpg"
    full = client.get("/api/library/lib00000001/stream", headers=h)
    assert full.status_code == 200 and len(full.content) == 1024 and full.headers["content-type"] == "video/mp4"
    assert full.headers["accept-ranges"] == "bytes"
    part = client.get("/api/library/lib00000001/stream", headers={**h, "Range": "bytes=10-19"})
    assert part.status_code == 206 and part.content == bytes(range(10, 20))
    assert part.headers["content-range"] == "bytes 10-19/1024"
    assert client.get("/api/library?q=nomatch", headers=h).json()["total"] == 0
    assert client.get("/api/library?channel_id=UCa&sort=size", headers=h).json()["total"] == 1
    assert client.get("/api/library/unknown0001/stream", headers=h).status_code == 404


def test_library_stream_rejects_path_escape(client, env, tmp_path):
    h = login(client)
    secret = env["data"] / "secret.txt"
    secret.write_text("top secret")
    os.symlink(env["data"], env["archive"] / "link")
    cases = {
        "esc00000001": "../data/secret.txt",
        "esc00000002": str(secret),
        "esc00000003": "link/secret.txt",
        "esc00000004": "Alpha/../../data/secret.txt",
    }
    for vid, rel in cases.items():
        seed_video(h.user_id, vid, archived_file=(env["archive"], rel))
        r = client.get(f"/api/library/{vid}/stream", headers=h)
        assert r.status_code == 404, rel
        assert b"top secret" not in r.content
    # a row whose root_path is outside the mounts is refused too
    seed_video(h.user_id, "esc00000005", archived_file=(env["data"], "secret.txt"))
    assert client.get("/api/library/esc00000005/stream", headers=h).status_code == 404
    # file recorded in the DB but gone from disk
    seed_video(h.user_id, "esc00000006", archived_file=(env["archive"], "missing.mp4"))
    assert client.get("/api/library/esc00000006/stream", headers=h).json()["error"]["code"] == "file_missing"


# --------------------------------------------------------------------------- dashboard / quota / logs


def test_dashboard_quota_and_logs(client, env):
    admin = login(client, "owner@example.com")
    other = login(client, "second@example.com")
    assert other.user_id != admin.user_id
    d = client.get("/api/dashboard", headers=admin).json()
    assert d["account_status"] == "active" and d["likes_mode"] == "future_only" and d["storage"]["writable"] is True
    assert d["quota"]["budget"] == 9000 and d["quota"]["used"] == 0 and d["quota"]["exhausted"] is False
    q = client.get("/api/quota", headers=admin).json()
    assert q["resets_at"] and q["day"] == d["quota"]["day"]
    (env["logs"] / "downtube.log").write_text("line one\nrefresh_token=SECRETVALUE123 ok\nBearer ya29.abcdef\n")
    r = client.get("/api/logs?lines=2", headers=admin).json()["lines"]
    assert len(r) == 2 and "SECRETVALUE123" not in "".join(r) and "ya29.abcdef" not in "".join(r)
    assert client.get("/api/logs", headers=other).status_code == 403
    assert client.get("/api/logs?lines=0", headers=admin).status_code == 422


# --------------------------------------------------------------------------- disconnect


def test_disconnect_with_local_data_deletion_keeps_media_and_other_users(client, env):
    a, b = login(client, "owner@example.com"), login(client, "second@example.com")
    f = env["archive"] / "shared.mp4"
    f.write_bytes(b"x")
    seed_channel(a.user_id, "UCa", mode=ArchiveMode.follow_new)
    seed_channel(b.user_id, "UCa")
    seed_video(a.user_id, "shared00001", archived_file=(env["archive"], "shared.mp4"))
    with write_session() as s:  # B also likes the shared video
        s.add(VideoSource(user_id=b.user_id, video_id="shared00001", source=DiscoverySource.like))
    seed_video(a.user_id, "onlya000001", job=JobStatus.queued)
    seed_video(a.user_id, "onlya000002")
    with respx.mock() as r:
        revoke = r.post("https://oauth2.googleapis.com/revoke").respond(200)
        res = client.post(
            "/api/auth/disconnect", json={"revoke_google_token": True, "delete_local_data": True}, headers=a
        )
        assert res.status_code == 204 and revoke.call_count == 1
        assert b"token=1%2F%2Ftest-refresh" in revoke.calls.last.request.content
    with read_session() as s:
        for model in (Subscription, ChannelRule, GoogleAccount, SyncRun):
            assert not [x for x in s.scalars(select(model)) if x.user_id == a.user_id], model
        assert not s.scalars(select(WebSession).where(WebSession.user_id == a.user_id)).all()
        assert not s.scalars(select(VideoSource).where(VideoSource.user_id == a.user_id)).all()
        # media is untouched; the shared video stays visible to B; orphaned metadata is gone
        assert s.scalar(select(MediaFile).where(MediaFile.video_id == "shared00001")) is not None
        assert s.get(Video, "shared00001") is not None and s.get(Video, "onlya000002") is None
        assert s.scalars(select(VideoSource).where(VideoSource.user_id == b.user_id)).all()
        assert s.get(Channel, "UCa") is not None  # still referenced by B
        # the job nobody wants any more was cancelled but its record survives
        assert s.scalar(select(DownloadJob).where(DownloadJob.video_id == "onlya000001")).status == JobStatus.cancelled
        assert s.scalar(select(AuditEvent).where(AuditEvent.action == "account.disconnect")) is not None
        assert s.get(User, a.user_id) is not None
    assert client.get("/api/auth/me", headers=a).status_code == 401
    assert client.get("/api/library", headers=b).json()["total"] == 1


def test_disconnect_keeps_data_by_default_and_wipes_tokens(client):
    a = login(client)
    seed_channel(a.user_id, mode=ArchiveMode.follow_new)
    with respx.mock() as r:
        r.post("https://oauth2.googleapis.com/revoke").respond(400)  # revocation failure must not block
        res = client.post("/api/auth/disconnect", json={"revoke_google_token": True}, headers=a)
    assert res.status_code == 204
    with read_session() as s:
        acc = s.scalar(select(GoogleAccount))
        assert acc.status.value == "disconnected" and acc.access_token_enc is None and acc.refresh_token_enc is None
        assert s.scalar(select(ChannelRule)) is not None
    assert client.get("/api/auth/me", headers=a).json()["account_status"] == "disconnected"


def test_auth_me_and_acknowledge(client):
    h = login(client, acknowledged=False)
    me = client.get("/api/auth/me", headers=h).json()
    assert me["acknowledged_terms"] is False and me["email"] == "owner@example.com" and me["is_admin"] is True
    assert client.post("/api/auth/acknowledge", headers=h).json()["acknowledged_terms"] is True
    assert httpx and Path
