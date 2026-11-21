import os
from datetime import UTC, datetime

import pytest

from downtube.models import (
    Channel,
    ChannelRule,
    DiscoverySource,
    JobKind,
    JobStatus,
    MediaFile,
    User,
    Video,
    VideoSource,
)


def test_sanitize_component():
    from downtube.storage.paths import sanitize_component

    assert sanitize_component('a/b\\c:d*e?"f<g>h|i') == "a b c d e f g h i"
    assert sanitize_component("..hidden. ") == "hidden"
    assert sanitize_component("Émission — 東京 🎥") == "Émission — 東京 🎥"
    assert sanitize_component("") == "untitled"
    assert sanitize_component("CON") == "CON untitled"
    long = sanitize_component("é" * 300, max_bytes=180)
    assert len(long.encode()) <= 180


def test_resolve_within_mounts_blocks_escape(env, tmp_path):
    from downtube.storage.paths import StorageError, resolve_within_mounts

    archive = env["archive"]
    (archive / "ok").mkdir()
    assert resolve_within_mounts(str(archive / "ok")) == (archive / "ok").resolve()
    with pytest.raises(StorageError):
        resolve_within_mounts(str(archive / ".." / "data"))
    os.symlink(tmp_path, archive / "escape")
    with pytest.raises(StorageError):
        resolve_within_mounts(str(archive / "escape"))
    with pytest.raises(StorageError):
        resolve_within_mounts("relative/path")


def test_browse_hides_escaping_symlinks(env, tmp_path):
    from downtube.storage.paths import browse

    (env["archive"] / "Movies").mkdir()
    os.symlink(tmp_path, env["archive"] / "evil")
    res = browse(None)
    assert [e.name for e in res.entries] == ["Movies"] and res.parent is None


def _seed(db, cap_rule=720):
    u = User(google_sub="s1", email="owner@example.com")
    ch = Channel(id="UCx", title="Chan")
    db.add_all([u, ch])
    db.flush()
    rule = ChannelRule(user_id=u.id, channel_id="UCx", max_height=cap_rule)
    v = Video(id="abcdefghijk", channel_id="UCx", title="T", published_at=datetime(2026, 1, 2, tzinfo=UTC))
    db.add_all([rule, v])
    db.flush()
    db.add(VideoSource(user_id=u.id, video_id=v.id, source=DiscoverySource.subscription, channel_rule_id=rule.id))
    db.flush()
    return u, ch, rule, v


def test_channel_folder_stable_and_collision(db):
    from downtube.storage.paths import channel_folder_name, video_filename

    a = Channel(id="UCa", title="Same Name")
    b = Channel(id="UCb", title="Same Name")
    db.add_all([a, b])
    db.flush()
    assert channel_folder_name(db, a) == "Same Name"
    assert channel_folder_name(db, b) == "Same Name [UCb]"
    a.title = "Renamed"
    assert channel_folder_name(db, a) == "Same Name"
    v = Video(id="abcdefghijk", title="Hello: World?", published_at=datetime(2026, 10, 15, tzinfo=UTC))
    assert video_filename(v, "mp4") == "2026-10-15 - Hello World [abcdefghijk].mp4"


def test_enqueue_dedupe_and_like_precedence(db):
    from downtube.downloads.jobs import PRIORITY_LIKE, compute_effective_cap, enqueue

    u, _, _, v = _seed(db)
    assert compute_effective_cap(db, v.id) == 720
    j1 = enqueue(db, v.id, user_id=u.id)
    j2 = enqueue(db, v.id, user_id=u.id)
    assert j1 is j2 and j1.max_height == 720
    # liking the video raises the cap of the pending job to 2160 and its priority
    db.add(VideoSource(user_id=u.id, video_id=v.id, source=DiscoverySource.like))
    db.flush()
    j3 = enqueue(db, v.id, user_id=u.id, priority=PRIORITY_LIKE)
    assert j3 is j1 and j1.max_height == 2160 and j1.priority == PRIORITY_LIKE


def test_upgrade_job_when_liked_after_archive(db):
    from downtube.downloads.jobs import enqueue

    u, _, _, v = _seed(db)
    db.add(
        MediaFile(
            video_id=v.id,
            root_path="/archive",
            rel_path="Chan/x.mp4",
            container="mp4",
            width=1280,
            height=720,
            size_bytes=1,
            max_height_requested=720,
            best_available_height=2160,
        )
    )
    db.flush()
    assert enqueue(db, v.id, user_id=u.id) is None  # already archived at the channel cap
    db.add(VideoSource(user_id=u.id, video_id=v.id, source=DiscoverySource.like))
    db.flush()
    job = enqueue(db, v.id, user_id=u.id)
    assert job is not None and job.kind == JobKind.upgrade and job.max_height == 2160


def test_no_upgrade_when_source_already_best(db):
    from downtube.downloads.jobs import enqueue

    u, _, _, v = _seed(db)
    db.add(
        MediaFile(
            video_id=v.id,
            root_path="/archive",
            rel_path="Chan/x.mp4",
            container="mp4",
            width=854,
            height=480,
            size_bytes=1,
            max_height_requested=720,
            best_available_height=480,
        )
    )
    db.add(VideoSource(user_id=u.id, video_id=v.id, source=DiscoverySource.like))
    db.flush()
    assert enqueue(db, v.id, user_id=u.id) is None  # 480p source stays 480p, never "upgraded"


def test_transitions(db):
    from downtube.downloads.jobs import JobStateError, cancel_job, enqueue, pause_job, resume_job, retry_job

    u, _, _, v = _seed(db)
    j = enqueue(db, v.id, user_id=u.id)
    pause_job(db, j)
    assert j.status == JobStatus.paused
    resume_job(db, j)
    cancel_job(db, j)
    assert j.status == JobStatus.cancelled
    with pytest.raises(JobStateError):
        pause_job(db, j)
    retry_job(db, j)
    assert j.status == JobStatus.queued and j.attempts == 0
