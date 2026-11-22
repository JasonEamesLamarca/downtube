"""Import media files the user downloaded themselves (for example age-restricted videos).

DownTube never handles YouTube cookies. For videos that YouTube only serves to a signed-in
account, the user runs yt-dlp on their own computer (where they are signed in) and hands the
file to DownTube, either by uploading it on the Downloads page or by copying it into the
import folder ``<archive root>/.import``. The file is then checked like any download:

* it must be a playable MP4/MKV with a video stream (ffprobe);
* its duration must match the video's YouTube duration;
* if it carries YouTube metadata (yt-dlp ``--embed-metadata``), that must name the same video;
* it must not exceed the resolution limit that applies to the video.

Accepted files are moved into the archive with the usual name and sidecars, never re-encoded.
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import delete, select

from downtube.auth.audit import audit
from downtube.config import get_config
from downtube.db import read_session, write_session
from downtube.downloads import jobs
from downtube.downloads.manager import finalize_into_archive
from downtube.downloads.policy import Fmt, Selection
from downtube.downloads.validator import Probe, ValidationError, ffprobe_json, parse_ffprobe
from downtube.models import DownloadJob, JobKind, JobStage, JobStatus, Video, VideoExclusion, VideoSource
from downtube.settings_service import get_settings
from downtube.storage import paths

log = logging.getLogger("downtube.import")

IMPORT_DIR_NAME = ".import"
UPLOAD_DIR_NAME = ".uploading"
MEDIA_SUFFIXES = {".mp4", ".m4v", ".mkv", ".webm", ".mov"}
STABLE_AGE_S = 30  # a file must be unchanged this long before the folder scan takes it
THUMB_MAX_BYTES = 5 * 1024 * 1024
_ID_IN_NAME = re.compile(r"\[([A-Za-z0-9_-]{11})\]")
_ID_IN_TAG = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")
_THUMB_URL = re.compile(r"^https://([a-z0-9-]+\.)*(ytimg\.com|ggpht\.com)/", re.I)
_RETRYABLE_STATES = (JobStatus.failed, JobStatus.cancelled, JobStatus.unsupported, JobStatus.skipped)


class ImportFailed(Exception):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# --------------------------------------------------------------------------- folders


def archive_root() -> Path:
    with read_session() as s:
        root = paths.resolve_within_mounts(get_settings(s).archive_root)
    root.mkdir(parents=True, exist_ok=True)
    return root


def import_dir(root: Path | None = None) -> Path:
    """``<archive root>/.import`` (created on demand; a ``.plexignore`` keeps media servers out)."""
    d = (root or archive_root()) / IMPORT_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    ignore = d / ".plexignore"
    if not ignore.exists():
        try:
            ignore.write_text("*\n", encoding="utf-8")
        except OSError:
            pass
    return d


def upload_dir(root: Path | None = None) -> Path:
    d = import_dir(root) / UPLOAD_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def host_import_path() -> str:
    """The import folder as the user sees it on the host (when DOWNTUBE_HOST_ARCHIVE_PATH is set)."""
    cfg = get_config()
    with read_session() as s:
        root = get_settings(s).archive_root
    host = cfg.host_archive_path
    if host and root.rstrip("/") == cfg.archive_root.rstrip("/"):
        return f"{host.rstrip('/')}/{IMPORT_DIR_NAME}"
    return f"{root.rstrip('/')}/{IMPORT_DIR_NAME}"


def clean_stale_uploads() -> None:
    """Remove half-finished uploads left by a restart."""
    try:
        d = import_dir() / UPLOAD_DIR_NAME
    except Exception:  # noqa: BLE001 - archive not reachable yet; nothing to clean
        return
    if d.is_dir():
        for f in d.iterdir():
            if f.is_file():
                f.unlink(missing_ok=True)


def video_id_from_name(name: str) -> str | None:
    m = _ID_IN_NAME.findall(name)
    return m[-1] if m else None


# --------------------------------------------------------------------------- checks (patchable in tests)


def probe_file(path: Path) -> tuple[Probe, dict[str, str]]:
    """ffprobe the file; return the parsed probe and the container tags (lower-case keys)."""
    data = ffprobe_json(path, get_config().ffprobe_bin)
    probe = parse_ffprobe(data, path)
    tags = (data.get("format") or {}).get("tags") or {}
    return probe, {str(k).lower(): str(v) for k, v in tags.items()}


def fetch_thumbnail(url: str | None, dest: Path) -> bool:
    """Download the YouTube thumbnail (only from YouTube's image hosts). Best effort."""
    if not url or not _THUMB_URL.match(url):
        return False
    try:
        with httpx.Client(timeout=15.0, follow_redirects=False) as c, c.stream("GET", url) as r:
            if r.status_code != 200:
                return False
            data = b""
            for chunk in r.iter_bytes():
                data += chunk
                if len(data) > THUMB_MAX_BYTES:
                    return False
        dest.write_bytes(data)
        return True
    except (httpx.HTTPError, OSError):
        return False


def tagged_video_ids(tags: dict[str, str]) -> set[str]:
    """Video IDs named by yt-dlp's ``purl``/``comment`` tags (both hold the watch URL).

    The description is deliberately ignored: it is free text that may link to other videos.
    """
    found: set[str] = set()
    for key in ("purl", "comment"):
        if key in tags:
            found.update(_ID_IN_TAG.findall(tags[key]))
    return found


# --------------------------------------------------------------------------- import


@dataclass
class _Target:
    job_id: int
    user_id: int
    cap: int
    previous: dict[str, Any] | None  # job fields to restore if the import fails (None = job was created)


def _claim_target(path: Path, video_id: str, job_id: int | None, user_id: int | None, origin: str) -> _Target:
    """Pick (or create) the job the import completes and mark it running so no worker touches it."""
    with write_session() as s:
        video = s.get(Video, video_id)
        if video is None:
            raise ImportFailed(
                "video_unknown", f"video {video_id} is not in DownTube; add it on the Downloads page first"
            )
        if jobs.current_media(s, video_id) is not None:
            raise ImportFailed("already_archived", "this video is already in the archive", 409)
        job: DownloadJob | None
        if job_id is not None:
            job = s.get(DownloadJob, job_id)
            if job is None or job.video_id != video_id:
                raise ImportFailed("not_found", "download not found", 404)
        else:
            job = jobs.active_job(s, video_id) or s.scalar(
                select(DownloadJob)
                .where(DownloadJob.video_id == video_id, DownloadJob.status.in_(_RETRYABLE_STATES))
                .order_by(DownloadJob.id.desc())
                .limit(1)
            )
        if job is not None and job.status == JobStatus.running:
            raise ImportFailed("job_running", "this video is downloading right now; wait for it to finish or fail", 409)
        if job is not None and job.status == JobStatus.completed:
            raise ImportFailed("already_archived", "this download already completed", 409)
        if user_id is None:
            user_id = (job.user_id if job is not None else None) or s.scalar(
                select(VideoSource.user_id).where(VideoSource.video_id == video_id).limit(1)
            )
        if user_id is None:
            raise ImportFailed("video_unknown", f"video {video_id} is not in anyone's library")

        cap = job.max_height if job is not None else (jobs.compute_effective_cap(s, video_id) or jobs.cap(None))
        info = {"imported": True, "origin": origin, "original_name": path.name[:300]}
        if job is None:
            job = DownloadJob(
                video_id=video_id,
                user_id=user_id,
                kind=JobKind.archive,
                priority=jobs.PRIORITY_MANUAL,
                max_height=cap,
                status=JobStatus.running,
                stage=JobStage.finalizing,
                selection=info,
                started_at=datetime.now(UTC),
            )
            s.add(job)
            s.flush()
            return _Target(job.id, user_id, cap, None)
        previous = {
            "status": job.status,
            "stage": job.stage,
            "error_code": job.error_code,
            "error_message": job.error_message,
            "selection": job.selection,
        }
        job.status, job.stage = JobStatus.running, JobStage.finalizing
        job.error_code = job.error_message = None
        job.selection = info
        job.started_at = datetime.now(UTC)
        return _Target(job.id, user_id, cap, previous)


def _release(target: _Target) -> None:
    with write_session() as s:
        job = s.get(DownloadJob, target.job_id)
        if job is None:
            return
        if target.previous is None:
            s.delete(job)
        else:
            for k, v in target.previous.items():
                setattr(job, k, v)


def _check(probe: Probe, tags: dict[str, str], video: Video, cap: int) -> None:
    if probe.container not in ("mp4", "mkv"):
        raise ImportFailed("unsupported_container", f"container {probe.format_names!r} is not MP4 or MKV/WebM")
    others = tagged_video_ids(tags) - {video.id}
    if others:
        raise ImportFailed("wrong_video", f"the file's metadata says it is video {sorted(others)[0]}, not {video.id}")
    if probe.short_side > cap:
        raise ImportFailed(
            "too_large", f"resolution {probe.short_side}p is above the {cap}p limit that applies to this video"
        )
    if video.duration_s and probe.duration_s is not None:
        tolerance = max(2.0, video.duration_s * 0.02)
        if abs(probe.duration_s - video.duration_s) > tolerance:
            raise ImportFailed(
                "duration_mismatch",
                f"the file lasts {probe.duration_s:.0f}s but the video lasts {video.duration_s}s "
                "(wrong video or incomplete file?)",
            )


def import_file(
    path: Path, video_id: str, *, origin: str, user_id: int | None = None, job_id: int | None = None
) -> int:
    """Validate ``path`` and move it into the archive as ``video_id``. Returns the job id.

    On success the file is gone from ``path``; on failure it is left untouched and the job is
    restored to its previous state. Raises :class:`ImportFailed`.
    """
    try:
        probe, tags = probe_file(path)
    except ValidationError as e:
        raise ImportFailed("invalid_file", f"not a playable video file: {e.message}") from e

    target = _claim_target(path, video_id, job_id, user_id, origin)
    tmp = get_config().temp_dir / "imports" / str(target.job_id)
    try:
        with read_session() as s:
            video = s.get(Video, video_id)
            assert video is not None
            st = get_settings(s)
            thumb_url = video.thumbnail_url
            _check(probe, tags, video, target.cap)
        root = paths.resolve_within_mounts(st.archive_root)
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        if st.write_thumbnails:
            fetch_thumbnail(thumb_url, tmp / "media.jpg")
        selection = Selection(
            video=Fmt(
                format_id="manual-import",
                ext=probe.container,
                vcodec=probe.vcodec or "unknown",
                acodec="none",
                width=probe.width,
                height=probe.height,
                fps=probe.fps,
            ),
            audio=None,
            container="mp4" if probe.container == "mp4" else "mkv",
            effective_cap=target.cap,
            source_best_height=probe.short_side,
            reasons=[f"imported by the user ({origin}); not downloaded by DownTube"],
        )
        finalize_into_archive(target.job_id, video_id, selection, probe, path, tmp, root, st, probe.short_side)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        _release(target)
        raise

    with write_session() as s:
        # An explicit import overrides an earlier "deleted from the library" decision.
        s.execute(
            delete(VideoExclusion).where(VideoExclusion.user_id == target.user_id, VideoExclusion.video_id == video_id)
        )
        job = s.get(DownloadJob, target.job_id)
        if job is not None:
            job.selection = (job.selection or {}) | {"height": probe.short_side}
        audit(
            s, "download.import", target.user_id, {"video_id": video_id, "origin": origin, "height": probe.short_side}
        )
    log.info("imported %s for %s (%s, %sp)", path.name, video_id, origin, probe.short_side)
    return target.job_id


# --------------------------------------------------------------------------- folder scan


@dataclass
class FolderFile:
    name: str
    size_bytes: int
    video_id: str | None
    status: str  # waiting | failed | imported
    message: str | None = None


_failures: dict[str, tuple[int, float, str]] = {}  # name -> (size, mtime, message)
_scan_lock = threading.Lock()


def _candidates(d: Path) -> list[Path]:
    return sorted(
        f for f in d.iterdir() if f.is_file() and not f.name.startswith(".") and f.suffix.lower() in MEDIA_SUFFIXES
    )


def list_folder() -> list[FolderFile]:
    """Files currently waiting in the import folder, with the last problem seen for each."""
    d = import_dir()
    out = []
    for f in _candidates(d):
        st = f.stat()
        fail = _failures.get(f.name)
        failed = fail is not None and fail[0] == st.st_size and fail[1] == st.st_mtime
        out.append(
            FolderFile(
                f.name,
                st.st_size,
                video_id_from_name(f.name),
                "failed" if failed else "waiting",
                fail[2] if failed and fail else None,
            )
        )
    return out


def scan_folder(*, force: bool = False, now: float | None = None) -> list[FolderFile]:
    """Import every stable file in the import folder whose name contains ``[VIDEO_ID]``.

    Files that failed before are retried only when they changed, or when ``force`` is set
    (the "Scan now" button).
    """
    if not _scan_lock.acquire(blocking=False):
        return list_folder()
    try:
        d = import_dir()
        results: list[FolderFile] = []
        now = now if now is not None else time.time()
        names = set()
        for f in _candidates(d):
            names.add(f.name)
            st = f.stat()
            vid = video_id_from_name(f.name)
            if now - st.st_mtime < STABLE_AGE_S:
                results.append(FolderFile(f.name, st.st_size, vid, "waiting", "still being copied"))
                continue
            prev = _failures.get(f.name)
            if prev and not force and prev[0] == st.st_size and prev[1] == st.st_mtime:
                results.append(FolderFile(f.name, st.st_size, vid, "failed", prev[2]))
                continue
            if vid is None:
                msg = "the file name must contain the video ID in brackets, e.g. [dQw4w9WgXcQ]"
                _failures[f.name] = (st.st_size, st.st_mtime, msg)
                results.append(FolderFile(f.name, st.st_size, None, "failed", msg))
                continue
            try:
                import_file(f, vid, origin="folder")
            except ImportFailed as e:
                _failures[f.name] = (st.st_size, st.st_mtime, e.message)
                results.append(FolderFile(f.name, st.st_size, vid, "failed", e.message))
                log.warning("import of %s failed: %s", f.name, e.message)
            except (OSError, paths.StorageError) as e:
                msg = f"could not move the file into the archive: {e}"
                _failures[f.name] = (st.st_size, st.st_mtime, msg)
                results.append(FolderFile(f.name, st.st_size, vid, "failed", msg))
                log.warning("import of %s failed: %s", f.name, e)
            else:
                _failures.pop(f.name, None)
                results.append(FolderFile(f.name, st.st_size, vid, "imported"))
        for gone in set(_failures) - names:
            _failures.pop(gone, None)
        return results
    finally:
        _scan_lock.release()


def reset_state() -> None:
    """Forget remembered folder failures (tests)."""
    _failures.clear()
