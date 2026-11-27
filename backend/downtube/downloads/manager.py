"""Persistent download worker pool.

Jobs live in the ``download_jobs`` table (see CONTRACTS §4). Worker threads claim the
highest-priority runnable job, then: probe → select formats (policy) → download (yt-dlp,
stream copy) → validate (ffprobe) → finalize atomically into the archive.

Data-integrity rules enforced here:
* partial data lives only under ``<temp>/jobs/<job_id>/``; the archive only ever receives
  validated files, via ``<final>.downtube-partial`` + ``os.replace``;
* an upgrade never removes the previous copy before the replacement is in place;
* unknown files already present at a target path are never overwritten.
"""

from __future__ import annotations

import logging
import os
import random
import re
import shutil
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from sqlalchemy import select, update

from downtube import runtime_status
from downtube.config import get_config
from downtube.db import read_session, write_session
from downtube.downloads import bot_guard, jobs
from downtube.downloads.adapter import Cancelled, DownloaderAdapter, DownloadError, Progress, YtDlpAdapter
from downtube.downloads.policy import (
    Selection,
    Unsupported,
    best_available_height,
    parse_formats,
    select_formats,
    summarize_formats,
)
from downtube.downloads.validator import Probe, ValidationError, validate
from downtube.models import Availability, Channel, DownloadJob, JobKind, JobStage, JobStatus, MediaFile, Video
from downtube.schemas import SettingsOut
from downtube.settings_service import get_settings
from downtube.storage import paths

log = logging.getLogger("downtube.downloads")

PROGRESS_WRITE_INTERVAL = 2.0
MAX_BACKOFF_S = 6 * 3600
BOT_CHECK_MIN_BACKOFF_S = 1800
DISK_WAIT_S = 900
PARTIAL_SUFFIX = ".downtube-partial"

ValidateFn = Callable[[Path, Selection, float | None], Probe]


def utcnow() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- helpers


def in_download_window(window: str | None, tz: str, now: datetime | None = None) -> bool:
    if not window:
        return True
    start_s, end_s = window.split("-")
    local = (now or utcnow()).astimezone(ZoneInfo(tz)).time()
    start, end = dtime.fromisoformat(start_s), dtime.fromisoformat(end_s)
    if start == end:
        return True
    if start < end:
        return start <= local < end
    return local >= start or local < end  # window crosses midnight


def backoff_seconds(attempts: int, code: str, rng: random.Random | None = None) -> float:
    r = rng or random
    delay = min(2 ** max(attempts, 1) * 60, MAX_BACKOFF_S) * r.uniform(0.8, 1.2)  # noqa: S311 - jitter only
    if code == "bot_check":
        delay = max(delay, BOT_CHECK_MIN_BACKOFF_S)
    return delay


def job_temp_dir(job_id: int) -> Path:
    return get_config().temp_dir / "jobs" / str(job_id)


def _parse_upload_date(info: dict[str, Any]) -> datetime | None:
    for key in ("release_timestamp", "timestamp"):
        if info.get(key):
            try:
                return datetime.fromtimestamp(int(info[key]), UTC)
            except (TypeError, ValueError, OSError):
                pass
    d = info.get("upload_date")
    if d and len(str(d)) == 8:
        try:
            return datetime.strptime(str(d), "%Y%m%d").replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def place_file(src: Path, dest: Path) -> None:
    """Atomically place ``src`` at ``dest`` via ``dest + .downtube-partial`` (works across filesystems)."""
    partial = dest.with_name(dest.name + PARTIAL_SUFFIX)
    try:
        os.replace(src, partial)  # same filesystem: cheap rename
    except OSError:
        with open(src, "rb") as fi, open(partial, "wb") as fo:
            shutil.copyfileobj(fi, fo, length=8 * 1024 * 1024)
            fo.flush()
            os.fsync(fo.fileno())
        src.unlink(missing_ok=True)
    os.replace(partial, dest)
    _fsync_dir(dest.parent)


def unique_target(path: Path) -> Path:
    """Never clobber an unknown file: append ' (n)' before the extension if needed."""
    if not path.exists():
        return path
    for n in range(2, 1000):
        cand = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not cand.exists():
            return cand
    raise OSError(f"cannot find a free file name for {path}")


_XML_INVALID = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _xml_text(value: str | None) -> str:
    return escape(_XML_INVALID.sub("", value or ""))


def nfo_xml(video: Video, channel_title: str | None) -> str:
    """Kodi-style movie .nfo (read by Plex's NFO agent, Jellyfin, Emby and Kodi).

    ``<plot>`` holds the full YouTube description.
    """
    pub = video.published_at
    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        "<movie>",
        f"  <title>{_xml_text(video.title or video.id)}</title>",
        f"  <plot>{_xml_text(video.description)}</plot>",
    ]
    if pub:
        lines += [
            f"  <premiered>{pub:%Y-%m-%d}</premiered>",
            f"  <aired>{pub:%Y-%m-%d}</aired>",
            f"  <year>{pub:%Y}</year>",
        ]
    if video.duration_s:
        lines.append(f"  <runtime>{max(1, round(video.duration_s / 60))}</runtime>")
    if channel_title:
        lines.append(f"  <studio>{_xml_text(channel_title)}</studio>")
    lines += [
        f'  <uniqueid type="youtube" default="true">{_xml_text(video.id)}</uniqueid>',
        "  <tag>YouTube</tag>",
        "</movie>",
        "",
    ]
    return "\n".join(lines)


def write_nfo(media: Path, video: Video, channel_title: str | None) -> Path:
    """Write ``<media stem>.nfo`` next to the media file (atomic replace)."""
    target = media.with_name(media.stem + ".nfo")
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(nfo_xml(video, channel_title), encoding="utf-8")
    os.replace(tmp, target)
    return target


# --------------------------------------------------------------------------- finalize


def finalize_into_archive(
    job_id: int,
    video_id: str,
    selection: Selection,
    probe: Probe,
    media: Path,
    tmp: Path,
    root: Path,
    st: SettingsOut,
    best: int | None,
) -> None:
    """Move a validated file into the archive, write sidecars and record it (job → completed)."""
    ext = selection.container
    with write_session() as s:
        video = s.get(Video, video_id)
        assert video is not None
        channel = s.get(Channel, video.channel_id) if video.channel_id else None
        if channel is None:
            channel = s.get(Channel, "_unknown")
            if channel is None:
                channel = Channel(id="_unknown", title="Unknown Channel")
                s.add(channel)
                s.flush()
        folder = paths.channel_folder_name(s, channel)
        filename = paths.video_filename(video, ext)
        old = jobs.current_media(s, video_id)
        old_path = Path(old.root_path) / old.rel_path if old else None
        channel_title = channel.title
        snapshot = Video(
            id=video.id,
            title=video.title,
            description=video.description,
            published_at=video.published_at,
            duration_s=video.duration_s,
        )
        job = s.get(DownloadJob, job_id)
        assert job is not None
        available = (job.selection or {}).get("available_formats")

    if old_path is not None:
        try:  # DB-recorded paths are re-validated before any rename/delete
            old_path = paths.resolve_within_mounts(old_path)
        except paths.StorageError:
            log.warning("job %s: previous archive path is outside the storage mounts; leaving it untouched", job_id)
            old_path = None
    (root / folder).mkdir(parents=True, exist_ok=True)
    dest = paths.safe_join(root, folder, filename)
    superseded_action: str | None = None

    if old_path is not None and old_path.exists() and old_path.resolve() == dest.resolve():
        # Upgrade to the same file name: stage the new copy, then swap.
        staged = dest.with_name(dest.name + ".downtube-new")
        place_file(media, staged)
        if st.keep_superseded_copies:
            kept = unique_target(dest.with_name(f"{dest.stem}.superseded{dest.suffix}"))
            os.replace(dest, kept)
            superseded_action = f"kept:{kept.name}"
        else:
            superseded_action = "deleted"
        os.replace(staged, dest)
        _fsync_dir(dest.parent)
    else:
        dest = unique_target(dest)
        place_file(media, dest)  # new copy is in place before the old one is touched
        if old_path is not None and old_path.exists():
            if st.keep_superseded_copies:
                kept = unique_target(old_path.with_name(f"{old_path.stem}.superseded{old_path.suffix}"))
                os.replace(old_path, kept)
                superseded_action = f"kept:{kept.name}"
            else:
                old_path.unlink()
                superseded_action = "deleted"

    # Optional sidecars (Plex local assets / metadata).
    base = dest.with_suffix("")
    thumb = tmp / "media.jpg"
    if st.write_thumbnails and thumb.is_file():
        try:
            place_file(thumb, base.with_name(base.name + ".jpg"))
        except OSError:
            log.warning("job %s: could not write thumbnail", job_id)
    if st.write_nfo:
        try:
            write_nfo(dest, snapshot, channel_title)
        except OSError:
            log.warning("job %s: could not write .nfo", job_id)

    rel = dest.relative_to(root)
    with write_session() as s:
        prev = jobs.current_media(s, video_id)
        if prev is not None:
            prev.is_current = False
            prev.superseded_at = utcnow()
            prev.superseded_action = superseded_action
            s.flush()
        mf = MediaFile(
            video_id=video_id,
            root_path=str(root),
            rel_path=str(rel),
            container=ext,
            width=probe.width,
            height=probe.height,
            fps=probe.fps,
            vcodec=probe.vcodec,
            acodec=probe.acodec,
            duration_s=probe.duration_s,
            size_bytes=dest.stat().st_size,
            is_hdr=probe.is_hdr,
            source_format_id=selection.format_spec,
            source_format_note="; ".join(selection.reasons)[:4000],
            max_height_requested=selection.effective_cap,
            best_available_height=best,
            available_formats=available,
            is_current=True,
        )
        s.add(mf)
        s.flush()
        job = s.get(DownloadJob, job_id)
        assert job is not None
        job.status, job.stage = JobStatus.completed, JobStage.done
        job.progress_pct = 100.0
        job.speed_bps = None
        job.eta_s = 0
        job.media_file_id = mf.id
        job.finished_at = utcnow()
        job.error_code = job.error_message = None
    shutil.rmtree(tmp, ignore_errors=True)
    log.info("job %s completed: %s (%sx%s %s/%s)", job_id, rel, probe.width, probe.height, probe.vcodec, probe.acodec)


# --------------------------------------------------------------------------- manager


class DownloadManager:
    def __init__(
        self,
        adapter: DownloaderAdapter | None = None,
        *,
        validate_fn: ValidateFn | None = None,
        poll_interval: float = 5.0,
    ) -> None:
        cfg = get_config()
        self.adapter: DownloaderAdapter = adapter or YtDlpAdapter(cfg.ytdlp_bin)
        self.validate_fn: ValidateFn = validate_fn or (
            lambda p, sel, dur: validate(p, sel, dur, binary=cfg.ffprobe_bin)
        )
        self.poll_interval = poll_interval
        self._cond = threading.Condition()
        self._stopping = threading.Event()
        self._threads: list[threading.Thread] = []
        self._running: dict[int, threading.Event] = {}
        self._shutdown_ids: set[int] = set()
        self._lock = threading.Lock()
        self._control_thread: threading.Thread | None = None

    # -- lifecycle
    def start(self) -> None:
        self._stopping.clear()
        jobs.set_wake_callback(self.wake)
        self._control_thread = threading.Thread(target=self._control_loop, name="dt-dl-control", daemon=True)
        self._control_thread.start()
        self._ensure_workers()
        runtime_status.set_flag("worker", True)
        self._check_archive_writable()

    def stop(self, timeout: float = 30.0) -> None:
        self._stopping.set()
        with self._lock:
            for jid, ev in self._running.items():
                self._shutdown_ids.add(jid)
                ev.set()
        self.wake()
        deadline = time.monotonic() + timeout
        for t in self._threads:
            t.join(max(0.1, deadline - time.monotonic()))
        jobs.set_wake_callback(None)
        runtime_status.set_flag("worker", False)

    def wake(self) -> None:
        with self._cond:
            self._cond.notify_all()

    def active_count(self) -> int:
        with self._lock:
            return len(self._running)

    def current_speed_bps(self) -> float:
        with read_session() as s:
            speeds = s.scalars(select(DownloadJob.speed_bps).where(DownloadJob.status == JobStatus.running))
            return float(sum(x or 0 for x in speeds))

    # -- worker pool
    def _desired_workers(self) -> int:
        try:
            with read_session() as s:
                return get_settings(s).max_concurrent_downloads
        except Exception:  # noqa: BLE001
            return 2

    def _ensure_workers(self) -> None:
        desired = self._desired_workers()
        self._threads = [t for t in self._threads if t.is_alive()]
        while len(self._threads) < desired:
            idx = len(self._threads)
            t = threading.Thread(target=self._worker_loop, args=(idx,), name=f"dt-dl-{idx}", daemon=True)
            self._threads.append(t)
            t.start()

    def _worker_loop(self, idx: int) -> None:
        while not self._stopping.is_set():
            ran = False
            try:
                if idx < self._desired_workers():
                    ran = self.run_once() is not None
            except Exception:  # noqa: BLE001 - a worker must never die
                log.exception("download worker %s crashed while processing a job", idx)
            if not ran:
                with self._cond:
                    self._cond.wait(self.poll_interval)

    def _control_loop(self) -> None:
        last_check = 0.0
        while not self._stopping.is_set():
            with self._lock:
                running = dict(self._running)
            for jid, ev in running.items():
                if jobs.peek_signal(jid):
                    ev.set()
            if time.monotonic() - last_check > 60:
                last_check = time.monotonic()
                self._check_archive_writable()
                self._ensure_workers()
            self._stopping.wait(0.5)

    def _check_archive_writable(self) -> None:
        try:
            with read_session() as s:
                root = paths.resolve_within_mounts(get_settings(s).archive_root)
            runtime_status.set_flag("archive_writable", paths.is_writable(root))
        except Exception:  # noqa: BLE001
            runtime_status.set_flag("archive_writable", False)

    # -- claiming
    def claim(self, now: datetime | None = None) -> int | None:
        now = now or utcnow()
        with write_session() as s:
            st = get_settings(s)
            if not in_download_window(st.download_window, st.timezone, now):
                return None
            if bot_guard.paused_until(s, now) is not None:  # YouTube bot check: everything waits
                return None
            job = s.scalar(
                select(DownloadJob)
                .where(
                    DownloadJob.status == JobStatus.queued,
                    (DownloadJob.next_attempt_at.is_(None)) | (DownloadJob.next_attempt_at <= now),
                )
                .order_by(DownloadJob.priority, DownloadJob.created_at, DownloadJob.id)
                .limit(1)
            )
            if job is None:
                return None
            job.status = JobStatus.running
            job.stage = JobStage.probing
            job.attempts += 1
            job.started_at = now
            job.error_code = job.error_message = None
            return job.id

    def run_once(self) -> int | None:
        jid = self.claim()
        if jid is None:
            return None
        ev = threading.Event()
        with self._lock:
            self._running[jid] = ev
        try:
            self.process(jid, ev)
        finally:
            with self._lock:
                self._running.pop(jid, None)
                self._shutdown_ids.discard(jid)
        return jid

    # -- processing pipeline
    def process(self, job_id: int, cancel_event: threading.Event | None = None) -> None:
        ev = cancel_event or threading.Event()
        try:
            self._process(job_id, ev)
        except Cancelled:
            self._handle_stop(job_id)
        except DownloadError as e:
            self._handle_error(job_id, e.code, e.message, e.retryable)
        except ValidationError as e:
            shutil.rmtree(job_temp_dir(job_id), ignore_errors=True)  # corrupt output: start clean
            self._handle_error(job_id, "validation_failed", e.message, retryable=True, max_attempts=2)
        except paths.StorageError as e:
            self._handle_error(job_id, "storage", str(e), retryable=True)
        except OSError as e:
            code = "disk_space" if e.errno == 28 else "storage"
            self._handle_error(job_id, code, f"{type(e).__name__}: {e}", retryable=True)
        except Exception as e:  # noqa: BLE001
            log.exception("job %s failed unexpectedly", job_id)
            self._handle_error(job_id, "internal", f"{type(e).__name__}: {e}", retryable=True)

    def _set(self, job_id: int, **fields: Any) -> None:
        with write_session() as s:
            s.execute(update(DownloadJob).where(DownloadJob.id == job_id).values(updated_at=utcnow(), **fields))

    def _check_stop(self, job_id: int, ev: threading.Event) -> None:
        if ev.is_set() or jobs.peek_signal(job_id):
            raise Cancelled()

    def _process(self, job_id: int, ev: threading.Event) -> None:
        with read_session() as s:
            job = s.get(DownloadJob, job_id)
            assert job is not None
            video_id, max_height, kind = job.video_id, job.max_height, job.kind
            st: SettingsOut = get_settings(s)
        log.info("job %s: %s %s (cap %sp)", job_id, kind.value, video_id, max_height)

        # 1) probe + format selection (policy is pure; see policy.py)
        info = self.adapter.probe(video_id)
        with write_session() as s:
            bot_guard.record_success(s)
        self._check_stop(job_id, ev)
        fmts = parse_formats(info)
        best = best_available_height(fmts)
        self._update_video_from_info(video_id, info)
        result = select_formats(
            fmts,
            max_height,
            audio_pref=st.audio_preference,
            prefer_hdr=st.prefer_hdr,
            mkv_fallback=st.mkv_fallback,
        )
        with write_session() as s:
            mf = jobs.current_media(s, video_id)
            if mf is not None:
                mf.best_available_height = best
            job = s.get(DownloadJob, job_id)
            assert job is not None
            if isinstance(result, Unsupported):
                job.status, job.stage = JobStatus.unsupported, JobStage.done
                job.error_code, job.error_message = result.code, result.reason
                job.selection = {"available_formats": summarize_formats(fmts), "source_best_height": best}
                job.finished_at = utcnow()
                log.warning("job %s unsupported: %s", job_id, result.reason)
                return
            job.selection = result.to_dict() | {"available_formats": summarize_formats(fmts)}
            if mf is not None and mf.height >= result.height:
                job.status, job.stage = JobStatus.skipped, JobStage.done
                job.error_code = "already_archived"
                job.error_message = (
                    f"Existing archive ({mf.height}p) already matches the best original stream within the "
                    f"limit ({result.height}p)."
                )
                job.finished_at = utcnow()
                return
        selection = result

        # 2) disk-space protection (archive + temp)
        root = paths.resolve_within_mounts(st.archive_root)
        root.mkdir(parents=True, exist_ok=True)
        est = selection.estimated_bytes or self._estimate_bytes(info, selection)
        min_free = st.min_free_gb * 1024**3
        temp_root = get_config().temp_dir
        temp_root.mkdir(parents=True, exist_ok=True)
        if not paths.has_space_for(root, est, min_free) or not paths.has_space_for(temp_root, est, 0):
            self._defer_for_space(job_id, est, min_free)
            return

        # 3) download (yt-dlp merges with stream copy — no re-encoding)
        self._set(job_id, stage=JobStage.downloading, progress_pct=0.0)
        tmp = job_temp_dir(job_id)
        active = max(1, self.active_count())
        rate = st.bandwidth_limit_kbps // active if st.bandwidth_limit_kbps else None
        last_write = [0.0]

        def on_progress(p: Progress) -> None:
            if ev.is_set():
                return
            if p.stage == "merging":
                self._set(job_id, stage=JobStage.merging, progress_pct=100.0, speed_bps=None, eta_s=0)
                return
            now = time.monotonic()
            if now - last_write[0] < PROGRESS_WRITE_INTERVAL:
                return
            last_write[0] = now
            self._set(
                job_id,
                progress_pct=p.percent,
                bytes_downloaded=p.downloaded_bytes,
                bytes_total=p.total_bytes,
                speed_bps=p.speed_bps,
                eta_s=p.eta_s,
            )

        media = self.adapter.download(
            video_id,
            selection,
            tmp,
            rate_limit_kbps=rate,
            write_thumbnail=st.write_thumbnails,
            on_progress=on_progress,
            cancel_event=ev,
            embed_metadata=st.embed_metadata,
        )
        self._check_stop(job_id, ev)

        # 4) validate before anything touches the archive
        self._set(job_id, stage=JobStage.validating, speed_bps=None, eta_s=None)
        duration = info.get("duration")
        probe = self.validate_fn(media, selection, float(duration) if duration else None)
        self._check_stop(job_id, ev)

        # 5) finalize atomically
        self._set(job_id, stage=JobStage.finalizing)
        self._finalize(job_id, video_id, selection, probe, media, tmp, root, st, best)

    @staticmethod
    def _estimate_bytes(info: dict[str, Any], sel: Selection) -> int:
        duration = float(info.get("duration") or 0)
        kbps = (sel.video.tbr or 0) + ((sel.audio.tbr or sel.audio.abr or 0) if sel.audio else 0)
        if duration and kbps:
            return int(duration * kbps * 1000 / 8 * 1.05)
        return 0

    def _defer_for_space(self, job_id: int, est: int, min_free: int) -> None:
        with write_session() as s:
            job = s.get(DownloadJob, job_id)
            assert job is not None
            job.status, job.stage = JobStatus.queued, JobStage.pending
            job.attempts = max(0, job.attempts - 1)  # waiting for space is not a failed attempt
            job.next_attempt_at = utcnow() + timedelta(seconds=DISK_WAIT_S)
            job.error_code = "disk_space"
            job.error_message = (
                f"Waiting for free space: needs ~{est / 1024**3:.1f} GB while keeping at least "
                f"{min_free / 1024**3:.0f} GB free."
            )
        log.warning("job %s deferred: insufficient disk space", job_id)

    @staticmethod
    def _update_video_from_info(video_id: str, info: dict[str, Any]) -> None:
        with write_session() as s:
            v = s.get(Video, video_id)
            if v is None:
                return
            ch_id = info.get("channel_id")
            if ch_id and s.get(Channel, ch_id) is None:
                s.add(Channel(id=ch_id, title=info.get("channel") or info.get("uploader") or ch_id))
                s.flush()
            if not v.channel_id and ch_id:
                v.channel_id = ch_id
            v.channel_title = v.channel_title or info.get("channel") or info.get("uploader")
            v.title = v.title or info.get("title")
            v.description = v.description or info.get("description")
            v.published_at = v.published_at or _parse_upload_date(info)
            if v.duration_s is None and info.get("duration"):
                v.duration_s = int(info["duration"])
            v.thumbnail_url = v.thumbnail_url or info.get("thumbnail")
            if v.availability == Availability.unknown:
                v.availability = (
                    Availability.unlisted if info.get("availability") == "unlisted" else Availability.public
                )

    def _finalize(
        self,
        job_id: int,
        video_id: str,
        selection: Selection,
        probe: Probe,
        media: Path,
        tmp: Path,
        root: Path,
        st: SettingsOut,
        best: int | None,
    ) -> None:
        finalize_into_archive(job_id, video_id, selection, probe, media, tmp, root, st, best)

    # -- outcomes
    def _handle_stop(self, job_id: int) -> None:
        action = jobs.pop_signal(job_id)
        with write_session() as s:
            job = s.get(DownloadJob, job_id)
            assert job is not None
            job.speed_bps = None
            job.eta_s = None
            if action == "cancel":
                job.status, job.stage = JobStatus.cancelled, JobStage.done
                job.finished_at = utcnow()
                shutil.rmtree(job_temp_dir(job_id), ignore_errors=True)
            elif action == "pause":
                job.status, job.stage = JobStatus.paused, JobStage.pending
                job.attempts = max(0, job.attempts - 1)
            else:  # shutdown: resume after restart, keep partial data
                job.status, job.stage = JobStatus.queued, JobStage.pending
                job.attempts = max(0, job.attempts - 1)
        log.info("job %s stopped (%s)", job_id, action or "shutdown")

    def _handle_error(
        self, job_id: int, code: str, message: str, retryable: bool, max_attempts: int | None = None
    ) -> None:
        with write_session() as s:
            job = s.get(DownloadJob, job_id)
            if job is None:
                return
            limit = min(job.max_attempts, max_attempts) if max_attempts else job.max_attempts
            job.error_code, job.error_message = code, message[:4000]
            job.speed_bps = None
            job.eta_s = None
            if code == "unavailable":
                job.status, job.stage = JobStatus.skipped, JobStage.done
                job.finished_at = utcnow()
                v = s.get(Video, job.video_id)
                if v is not None:
                    v.availability = Availability.private if "private" in message.lower() else Availability.deleted
            elif code == "bot_check":
                # The block is on the server's IP, not this video: pause the whole queue and give
                # the attempt back, so a long block never fails jobs permanently.
                until = bot_guard.record_bot_check(s, utcnow())
                job.status, job.stage = JobStatus.queued, JobStage.pending
                job.attempts = max(job.attempts - 1, 0)
                job.next_attempt_at = until
                log.warning("YouTube bot check: all downloads paused until %s", until.isoformat(timespec="minutes"))
            elif retryable and job.attempts < limit:
                job.status, job.stage = JobStatus.queued, JobStage.pending
                job.next_attempt_at = utcnow() + timedelta(seconds=backoff_seconds(job.attempts, code))
            else:
                job.status, job.stage = JobStatus.failed, JobStage.done
                job.finished_at = utcnow()
                shutil.rmtree(job_temp_dir(job_id), ignore_errors=True)
        log.warning("job %s error %s (retryable=%s): %s", job_id, code, retryable, message[:300])


# --------------------------------------------------------------------------- startup recovery


def recover_on_startup() -> None:
    """Re-queue jobs interrupted by a restart and remove orphaned temp data."""
    with write_session() as s:
        for job in s.scalars(select(DownloadJob).where(DownloadJob.status == JobStatus.running)):
            job.status, job.stage = JobStatus.queued, JobStage.pending
            job.attempts = max(0, job.attempts - 1)
            job.speed_bps = None
            job.eta_s = None
        keep = {
            str(j)
            for j in s.scalars(
                select(DownloadJob.id).where(DownloadJob.status.in_((JobStatus.queued, JobStatus.paused)))
            )
        }
    base = get_config().temp_dir / "jobs"
    if base.is_dir():
        for d in base.iterdir():
            if d.is_dir() and d.name not in keep:
                shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- module API

_manager: DownloadManager | None = None


def get_manager() -> DownloadManager:
    global _manager
    if _manager is None:
        _manager = DownloadManager()
    return _manager


def startup(adapter: DownloaderAdapter | None = None) -> DownloadManager:
    global _manager
    recover_on_startup()
    if adapter is not None or _manager is None:
        _manager = DownloadManager(adapter)
    _manager.start()
    return _manager


def shutdown(timeout: float = 30.0) -> None:
    if _manager is not None:
        _manager.stop(timeout)


__all__ = [
    "DownloadManager",
    "finalize_into_archive",
    "JobKind",
    "backoff_seconds",
    "get_manager",
    "in_download_window",
    "place_file",
    "recover_on_startup",
    "shutdown",
    "startup",
]
