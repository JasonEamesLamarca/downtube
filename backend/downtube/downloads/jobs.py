"""Download-queue operations shared by the sync engines, the REST API and the worker.

This module only manipulates database state and in-process control signals; the
worker threads in ``downloads.manager`` execute jobs.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from downtube.models import (
    ACTIVE_JOB_STATUSES,
    Availability,
    ChannelRule,
    DiscoverySource,
    DownloadJob,
    JobKind,
    JobStage,
    JobStatus,
    MediaFile,
    Video,
    VideoExclusion,
    VideoSource,
)
from downtube.settings_service import get_value

MAX_HEIGHT = 2160

PRIORITY_LIKE = 10
PRIORITY_MANUAL = 20
PRIORITY_SUBSCRIPTION = 50
PRIORITY_BACKFILL = 80


class JobStateError(ValueError):
    """Requested transition is not allowed from the job's current status."""


# ------------------------------------------------------------------ control signals (worker <-> API)

_lock = threading.Lock()
_signals: dict[int, str] = {}  # job_id -> "cancel" | "pause"
_wake: Callable[[], None] | None = None


def set_wake_callback(fn: Callable[[], None] | None) -> None:
    global _wake
    _wake = fn


def wake() -> None:
    if _wake is not None:
        _wake()


def signal(job_id: int, action: str) -> None:
    with _lock:
        _signals[job_id] = action


def pop_signal(job_id: int) -> str | None:
    with _lock:
        return _signals.pop(job_id, None)


def peek_signal(job_id: int) -> str | None:
    with _lock:
        return _signals.get(job_id)


# ------------------------------------------------------------------ quality precedence


def cap(requested: int | None) -> int:
    return MAX_HEIGHT if requested is None else max(1, min(int(requested), MAX_HEIGHT))


def compute_effective_cap(s: Session, video_id: str, extra: int | None = None) -> int | None:
    """Highest cap among all active, eligible discovery sources (liked videos override channel caps).

    Returns None when no source currently wants the video archived.
    """
    caps: list[int] = []
    if extra is not None:
        caps.append(cap(extra))
    default_cap = get_value(s, "default_max_height")
    rows = s.execute(
        select(VideoSource, ChannelRule)
        .outerjoin(ChannelRule, ChannelRule.id == VideoSource.channel_rule_id)
        .where(
            VideoSource.video_id == video_id,
            VideoSource.active.is_(True),
            VideoSource.archive_eligible.is_(True),
            # a user who deleted the video from the library no longer wants it
            ~exists().where(VideoExclusion.user_id == VideoSource.user_id, VideoExclusion.video_id == video_id),
        )
    ).all()
    for src, rule in rows:
        if src.source == DiscoverySource.like:
            caps.append(cap(get_value(s, "likes_max_height")))
        elif src.source == DiscoverySource.subscription:
            caps.append(cap(rule.max_height if rule and rule.max_height else default_cap))
        else:  # manual
            caps.append(cap(default_cap))
    return max(caps) if caps else None


def current_media(s: Session, video_id: str) -> MediaFile | None:
    return s.scalar(select(MediaFile).where(MediaFile.video_id == video_id, MediaFile.is_current.is_(True)))


def active_job(s: Session, video_id: str) -> DownloadJob | None:
    return s.scalar(
        select(DownloadJob).where(DownloadJob.video_id == video_id, DownloadJob.status.in_(ACTIVE_JOB_STATUSES))
    )


def needs_upgrade(mf: MediaFile, effective: int) -> bool:
    target = effective if mf.best_available_height is None else min(mf.best_available_height, effective)
    return mf.height < target and (mf.max_height_requested or 0) < effective


def enqueue(
    s: Session,
    video_id: str,
    *,
    user_id: int | None,
    priority: int = PRIORITY_SUBSCRIPTION,
    max_height: int | None = None,
) -> DownloadJob | None:
    """Queue (or refresh) work for a video. Never duplicates: one active job per video.

    * existing active job → raise its cap / priority if needed, return it
    * already archived at the best quality the sources allow → None
    * archived at lower quality and a source now allows more → ``upgrade`` job
    """
    video = s.get(Video, video_id)
    if video is None or video.availability in (Availability.private, Availability.deleted):
        return None
    effective = compute_effective_cap(s, video_id, extra=max_height)
    if effective is None:
        return None

    job = active_job(s, video_id)
    if job is not None:
        if effective > job.max_height and job.status != JobStatus.running:
            job.max_height = effective
        job.priority = min(job.priority, priority)
        s.flush()
        return job

    mf = current_media(s, video_id)
    kind = JobKind.archive
    if mf is not None:
        if not needs_upgrade(mf, effective):
            return None
        kind = JobKind.upgrade

    job = DownloadJob(
        video_id=video_id,
        user_id=user_id,
        kind=kind,
        priority=priority,
        max_height=effective,
        status=JobStatus.queued,
        stage=JobStage.pending,
    )
    s.add(job)
    s.flush()
    wake()
    return job


# ------------------------------------------------------------------ user-initiated transitions


def _now() -> datetime:
    return datetime.now(UTC)


def cancel_job(s: Session, job: DownloadJob) -> DownloadJob:
    if job.status not in ACTIVE_JOB_STATUSES:
        raise JobStateError(f"cannot cancel a {job.status.value} job")
    if job.status == JobStatus.running:
        signal(job.id, "cancel")  # worker kills the process, cleans temp, sets final status
    else:
        job.status = JobStatus.cancelled
        job.finished_at = _now()
    s.flush()
    return job


def pause_job(s: Session, job: DownloadJob) -> DownloadJob:
    if job.status not in (JobStatus.queued, JobStatus.running):
        raise JobStateError(f"cannot pause a {job.status.value} job")
    if job.status == JobStatus.running:
        signal(job.id, "pause")  # worker stops the process and keeps partial data
    else:
        job.status = JobStatus.paused
    s.flush()
    return job


def resume_job(s: Session, job: DownloadJob) -> DownloadJob:
    if job.status != JobStatus.paused:
        raise JobStateError(f"cannot resume a {job.status.value} job")
    job.status = JobStatus.queued
    job.next_attempt_at = None
    s.flush()
    wake()
    return job


def retry_job(s: Session, job: DownloadJob) -> DownloadJob:
    if job.status not in (JobStatus.failed, JobStatus.cancelled, JobStatus.unsupported, JobStatus.skipped):
        raise JobStateError(f"cannot retry a {job.status.value} job")
    if active_job(s, job.video_id) is not None:
        raise JobStateError("another job for this video is already active")
    job.status = JobStatus.queued
    job.stage = JobStage.pending
    job.attempts = 0
    job.next_attempt_at = None
    job.error_code = job.error_message = None
    job.finished_at = None
    s.flush()
    wake()
    return job
