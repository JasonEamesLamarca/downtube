import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from downtube.db import read_session, write_session
from downtube.downloads import bot_guard, jobs
from downtube.downloads.adapter import Cancelled, DownloadError, Progress
from downtube.downloads.manager import DownloadManager, backoff_seconds, in_download_window, recover_on_startup
from downtube.downloads.policy import Selection
from downtube.downloads.validator import Probe, ValidationError
from downtube.models import (
    Channel,
    ChannelRule,
    DiscoverySource,
    DownloadJob,
    JobKind,
    JobStatus,
    MediaFile,
    Setting,
    User,
    Video,
    VideoSource,
)

from .yt_fixtures import ladder

VID = "abcdefghijk"


class FakeAdapter:
    def __init__(self, source_h=1080, error=None, block=None):
        self.source_h = source_h
        self.error = error
        self.block = block  # threading.Event: wait until set (simulates a long download)
        self.downloads = 0

    def probe(self, video_id):
        info = ladder(self.source_h)
        info |= {
            "id": video_id,
            "title": "Hello World",
            "channel_id": "UCchan",
            "channel": "My Channel",
            "upload_date": "20261015",
            "duration": 60,
        }
        return info

    def download(
        self, video_id, selection, out_dir, *, rate_limit_kbps, write_thumbnail, on_progress, cancel_event, **_kw
    ):
        if self.error:
            raise self.error
        out_dir.mkdir(parents=True, exist_ok=True)
        on_progress(Progress("downloading", 50, 100, 1000.0, 1, 50.0))
        if self.block is not None:
            while not self.block.is_set():
                if cancel_event.wait(0.05):
                    raise Cancelled()
        self.downloads += 1
        p = out_dir / f"media.{selection.container}"
        p.write_bytes(b"x" * 1000)
        (out_dir / "media.jpg").write_bytes(b"jpg")
        return p


def fake_validate(path: Path, sel: Selection, dur):
    v = sel.video
    return Probe(
        container=sel.container,
        width=v.width,
        height=v.height,
        fps=v.fps,
        vcodec=v.vfamily,
        acodec="aac",
        duration_s=dur,
        size_bytes=path.stat().st_size,
        is_hdr=False,
        format_names="mp4",
    )


def seed(cap=None, liked=False):
    with write_session() as s:
        u = User(google_sub="s", email="owner@example.com")
        s.add_all([u, Channel(id="UCchan", title="My Channel")])
        s.flush()
        rule = ChannelRule(user_id=u.id, channel_id="UCchan", max_height=cap)
        s.add_all([rule, Video(id=VID, channel_id="UCchan")])
        s.flush()
        s.add(VideoSource(user_id=u.id, video_id=VID, source=DiscoverySource.subscription, channel_rule_id=rule.id))
        if liked:
            s.add(VideoSource(user_id=u.id, video_id=VID, source=DiscoverySource.like))
        s.flush()
        job = jobs.enqueue(s, VID, user_id=u.id)
        return u.id, job.id


def set_setting(key, value):
    with write_session() as s:
        s.merge(Setting(key=key, value=value))


def get_job(jid):
    with read_session() as s:
        return s.get(DownloadJob, jid)


def current_mf():
    with read_session() as s:
        return s.scalar(select(MediaFile).where(MediaFile.video_id == VID, MediaFile.is_current.is_(True)))


@pytest.fixture
def mgr(client):
    set_setting("min_free_gb", 1)
    return lambda adapter: DownloadManager(adapter, validate_fn=fake_validate)


def test_happy_path_archives_under_channel_folder(mgr, env):
    _, jid = seed(cap=720)
    m = mgr(FakeAdapter(source_h=2160))
    assert m.run_once() == jid
    job = get_job(jid)
    assert job.status == JobStatus.completed, job.error_message
    mf = current_mf()
    assert mf.height == 720 and mf.rel_path == "My Channel/2026-10-15 - Hello World [abcdefghijk].mp4"
    final = env["archive"] / mf.rel_path
    assert final.is_file() and final.with_suffix(".jpg").is_file()
    assert not list(env["archive"].rglob("*.downtube-partial"))
    assert not (env["temp"] / "jobs" / str(jid)).exists()
    assert job.selection["height"] == 720 and job.selection["source_best_height"] == 720
    assert m.run_once() is None  # nothing else queued


def test_no_upscale_source_480_limit_4k(mgr):
    seed(cap=None)
    m = mgr(FakeAdapter(source_h=480))
    m.run_once()
    assert current_mf().height == 480


def test_duplicate_prevention_after_archive(mgr):
    uid, _ = seed()
    mgr(FakeAdapter()).run_once()
    with write_session() as s:
        assert jobs.enqueue(s, VID, user_id=uid) is None


def test_retryable_error_backoff(mgr):
    _, jid = seed()
    mgr(FakeAdapter(error=DownloadError("network", "timed out", True))).run_once()
    job = get_job(jid)
    assert job.status == JobStatus.queued and job.error_code == "network" and job.attempts == 1
    assert job.next_attempt_at.replace(tzinfo=UTC) > datetime.now(UTC) + timedelta(seconds=60)


def test_attempts_exhausted_and_non_retryable(mgr):
    _, jid = seed()
    with write_session() as s:
        s.get(DownloadJob, jid).max_attempts = 1
    mgr(FakeAdapter(error=DownloadError("network", "x", True))).run_once()
    assert get_job(jid).status == JobStatus.failed


def test_unavailable_is_skipped_not_retried(mgr):
    _, jid = seed()
    mgr(FakeAdapter(error=DownloadError("unavailable", "ERROR: Private video", False))).run_once()
    assert get_job(jid).status == JobStatus.skipped
    with read_session() as s:
        assert s.get(Video, VID).availability.value == "private"


def test_validation_failure_never_reaches_archive(mgr, env):
    _, jid = seed()

    def bad(path, sel, dur):
        raise ValidationError("duration mismatch")

    DownloadManager(FakeAdapter(), validate_fn=bad).run_once()
    job = get_job(jid)
    assert job.status == JobStatus.queued and job.error_code == "validation_failed"
    assert not [p for p in env["archive"].rglob("*") if p.is_file()]


def test_cancel_and_pause_running_job(mgr, env):
    _, jid = seed()
    block = threading.Event()
    m = mgr(FakeAdapter(block=block))
    t = threading.Thread(target=m.run_once)
    t.start()
    for _ in range(100):
        if get_job(jid).status == JobStatus.running and m.active_count():
            break
        threading.Event().wait(0.02)
    jobs.signal(jid, "pause")
    m._running[jid].set()  # what the control loop does
    t.join(5)
    assert get_job(jid).status == JobStatus.paused
    with write_session() as s:
        jobs.resume_job(s, s.get(DownloadJob, jid))
    t = threading.Thread(target=m.run_once)
    t.start()
    for _ in range(100):
        if m.active_count():
            break
        threading.Event().wait(0.02)
    with write_session() as s:
        jobs.cancel_job(s, s.get(DownloadJob, jid))
    m._running[jid].set()
    t.join(5)
    assert get_job(jid).status == JobStatus.cancelled
    assert not (env["temp"] / "jobs" / str(jid)).exists()


def test_restart_recovery(mgr, env):
    _, jid = seed()
    with write_session() as s:
        j = s.get(DownloadJob, jid)
        j.status, j.attempts = JobStatus.running, 1
    (env["temp"] / "jobs" / str(jid)).mkdir(parents=True)
    (env["temp"] / "jobs" / "999").mkdir()
    recover_on_startup()
    assert get_job(jid).status == JobStatus.queued and get_job(jid).attempts == 0
    assert (env["temp"] / "jobs" / str(jid)).exists() and not (env["temp"] / "jobs" / "999").exists()


def test_disk_space_protection_defers(mgr):
    _, jid = seed()
    set_setting("min_free_gb", 10**9)  # impossible threshold
    mgr(FakeAdapter()).run_once()
    job = get_job(jid)
    assert job.status == JobStatus.queued and job.error_code == "disk_space" and job.attempts == 0


def test_upgrade_after_like_keeps_old_until_new_validated(mgr, env):
    uid, jid = seed(cap=720)
    m = mgr(FakeAdapter(source_h=2160))
    m.run_once()
    old = current_mf()
    assert old.height == 720
    with write_session() as s:
        s.add(VideoSource(user_id=uid, video_id=VID, source=DiscoverySource.like))
        s.flush()
        up = jobs.enqueue(s, VID, user_id=uid, priority=jobs.PRIORITY_LIKE)
        assert up.kind == JobKind.upgrade and up.max_height == 2160
    m.run_once()
    new = current_mf()
    assert new.height == 2160 and new.id != old.id
    files = sorted(p.name for p in (env["archive"] / "My Channel").iterdir())
    assert "2026-10-15 - Hello World [abcdefghijk].mp4" in files
    assert "2026-10-15 - Hello World [abcdefghijk].superseded.mp4" in files
    with read_session() as s:
        assert s.get(MediaFile, old.id).superseded_action.startswith("kept:")


def test_existing_better_archive_skips(mgr):
    uid, jid = seed()
    m = mgr(FakeAdapter(source_h=1080))
    m.run_once()
    with write_session() as s:
        j = DownloadJob(video_id=VID, user_id=uid, kind=JobKind.upgrade, max_height=2160)
        s.add(j)
        s.flush()
        jid2 = j.id
    m.run_once()
    assert get_job(jid2).status == JobStatus.skipped


def test_unknown_file_never_overwritten(mgr, env):
    seed()
    d = env["archive"] / "My Channel"
    d.mkdir()
    (d / "2026-10-15 - Hello World [abcdefghijk].mp4").write_bytes(b"precious")
    mgr(FakeAdapter()).run_once()
    assert (d / "2026-10-15 - Hello World [abcdefghijk].mp4").read_bytes() == b"precious"
    assert current_mf().rel_path.endswith("[abcdefghijk] (2).mp4")


def test_priority_likes_first(mgr):
    _, jid = seed()
    with write_session() as s:
        s.add(Video(id="zzzzzzzzzzz", channel_id="UCchan"))
        s.flush()
        hi = DownloadJob(video_id="zzzzzzzzzzz", priority=jobs.PRIORITY_LIKE, max_height=2160)
        s.add(hi)
        s.flush()
        hid = hi.id
    assert mgr(FakeAdapter()).claim() == hid


def test_download_window():
    noon = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert in_download_window(None, "UTC", noon)
    assert in_download_window("09:00-17:00", "UTC", noon)
    assert not in_download_window("01:00-06:00", "UTC", noon)
    assert in_download_window("22:00-13:00", "UTC", noon)  # crosses midnight
    assert not in_download_window("10:00-12:00", "Europe/Madrid", noon)  # 13:00 local


def test_window_blocks_claim(mgr):
    seed()
    set_setting("download_window", "00:00-00:01")
    late = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert mgr(FakeAdapter()).claim(now=late) is None


def test_backoff_bounds():
    assert 96 <= backoff_seconds(1, "network") <= 144
    assert backoff_seconds(20, "network") <= 6 * 3600 * 1.2
    assert backoff_seconds(1, "bot_check") >= 1800


# --------------------------------------------------------------------------- bot-check pause


def bot_error():
    return DownloadError("bot_check", "Sign in to confirm you're not a bot", True)


def test_bot_check_pauses_all_downloads_and_keeps_the_attempt(mgr):
    _, jid = seed()
    m = mgr(FakeAdapter(error=bot_error()))
    assert m.run_once() == jid
    job = get_job(jid)
    assert job.status == JobStatus.queued and job.error_code == "bot_check"
    assert job.attempts == 0  # a block on the IP never uses up the job's retries
    with read_session() as s:
        until, strikes = bot_guard.status(s, datetime.now(UTC))
    assert strikes == 1 and until is not None
    assert timedelta(minutes=26) < until - datetime.now(UTC) < timedelta(minutes=34)
    # Another job that is due now still waits: the whole queue is paused.
    with write_session() as s:
        get = s.get(DownloadJob, jid)
        get.next_attempt_at = None
    assert m.claim() is None
    assert m.claim(now=until + timedelta(seconds=1)) == jid


def test_bot_check_pause_escalates_and_success_resets(mgr):
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    with write_session() as s:
        first = bot_guard.record_bot_check(s, t0)
        # While paused, more checks (other workers) do not escalate.
        assert bot_guard.record_bot_check(s, t0 + timedelta(minutes=1)) == first
        second = bot_guard.record_bot_check(s, first + timedelta(seconds=1))
        assert bot_guard.status(s, second - timedelta(seconds=1))[1] == 2
        assert second - first > timedelta(minutes=50)  # ~60 min
        bot_guard.record_success(s)
        assert bot_guard.status(s, t0) == (None, 0)
    assert bot_guard.pause_seconds(20) == bot_guard.MAX_PAUSE_S


def test_successful_probe_clears_bot_pause(mgr):
    _, jid = seed()
    with write_session() as s:
        bot_guard.record_bot_check(s, datetime(2020, 1, 1, tzinfo=UTC))  # expired pause, strikes=1
    assert mgr(FakeAdapter()).run_once() == jid
    with read_session() as s:
        assert bot_guard.status(s, datetime.now(UTC)) == (None, 0)


def test_retry_now_makes_held_jobs_due(mgr):
    _, jid = seed()
    m = mgr(FakeAdapter(error=bot_error()))
    m.run_once()
    assert m.claim() is None  # paused
    with write_session() as s:
        assert bot_guard.retry_now(s, datetime.now(UTC)) is True
        assert bot_guard.retry_now(s, datetime.now(UTC)) is False  # nothing left to end
    assert get_job(jid).next_attempt_at is None
    assert m.claim() == jid
