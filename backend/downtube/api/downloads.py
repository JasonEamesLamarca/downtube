from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from downtube.api.views import PageParams, job_out, user_video_ids
from downtube.auth.deps import current_user, request_db, require_acknowledged
from downtube.db import read_session, write_session
from downtube.downloads import bot_guard, jobs
from downtube.main import ApiError
from downtube.models import (
    AuditEvent,
    DiscoverySource,
    DownloadJob,
    JobStatus,
    User,
    Video,
    VideoExclusion,
    VideoSource,
)
from downtube.schemas import BotCheckRetryOut, JobCounts, JobOut, ManualEnqueueRequest, Page
from downtube.sync import common
from downtube.youtube.client import AuthRevoked, QuotaExceeded, YouTubeError, client_for_user
from downtube.youtube.parse import video_from_api

log = logging.getLogger("downtube.api")
router = APIRouter(tags=["downloads"])

Status = Literal["queued", "running", "paused", "completed", "failed", "cancelled", "unsupported", "skipped"]


@router.get("/downloads", response_model=Page[JobOut])
def list_downloads(
    status: Status | None = None,
    pp: PageParams = Depends(),
    user: User = Depends(current_user),
    db: Session = Depends(request_db),
) -> Page[JobOut]:
    base = select(DownloadJob).where(DownloadJob.video_id.in_(user_video_ids(user.id)))
    if status:
        base = base.where(DownloadJob.status == JobStatus(status))
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(base.order_by(DownloadJob.id.desc()).offset(pp.offset).limit(pp.page_size)).all()
    return Page[JobOut](items=[job_out(j) for j in rows], total=total, page=pp.page, page_size=pp.page_size)


@router.get("/downloads/counts", response_model=JobCounts)
def counts(user: User = Depends(current_user), db: Session = Depends(request_db)) -> JobCounts:
    res = {s.value: 0 for s in JobStatus}
    for status, n in db.execute(
        select(DownloadJob.status, func.count())
        .where(DownloadJob.video_id.in_(user_video_ids(user.id)))
        .group_by(DownloadJob.status)
    ):
        res[status.value] = n
    return JobCounts(**res)


def _ensure_video(user_id: int, video_id: str) -> None:
    with read_session() as s:
        if s.get(Video, video_id) is not None:
            return
    try:
        with client_for_user(user_id) as yt:
            items = yt.videos([video_id])
    except AuthRevoked as e:
        raise ApiError(409, "reauth_required", "reconnect your Google account first") from e
    except QuotaExceeded as e:
        raise ApiError(503, "quota_exceeded", "YouTube API quota exhausted; try after the daily reset") from e
    except YouTubeError as e:
        raise ApiError(503, "youtube_unavailable", f"YouTube API error: {e.reason or e.status}") from e
    if not items:
        raise ApiError(404, "video_not_found", "video not found or not accessible")
    with write_session() as w:
        common.upsert_video(w, video_from_api(items[0]))


@router.post("/downloads", response_model=JobOut)
def enqueue_manual(body: ManualEnqueueRequest, user: User = Depends(require_acknowledged)) -> JobOut:
    _ensure_video(user.id, body.video_id)
    with write_session() as w:
        src = common.get_source(w, user.id, body.video_id, DiscoverySource.manual)
        if src is None:
            w.add(VideoSource(user_id=user.id, video_id=body.video_id, source=DiscoverySource.manual))
        else:
            src.active, src.archive_eligible = True, True
        # An explicit request overrides an earlier "deleted from the library" decision.
        w.execute(
            delete(VideoExclusion).where(VideoExclusion.user_id == user.id, VideoExclusion.video_id == body.video_id)
        )
        w.flush()
        existed = jobs.active_job(w, body.video_id) is not None
        job = jobs.enqueue(w, body.video_id, user_id=user.id, priority=jobs.PRIORITY_MANUAL, max_height=body.max_height)
        if job is None:
            raise ApiError(409, "nothing_to_do", "video is already archived at the requested quality or unavailable")
        if body.max_height and not existed and job.max_height > body.max_height:
            job.max_height = body.max_height  # an explicit manual cap (the default-cap source would otherwise win)
        return job_out(job)


def _action(job_id: int, user: User, fn) -> JobOut:  # type: ignore[no-untyped-def]
    with write_session() as w:
        job = w.scalar(
            select(DownloadJob).where(DownloadJob.id == job_id, DownloadJob.video_id.in_(user_video_ids(user.id)))
        )
        if job is None:
            raise ApiError(404, "not_found", "download not found")
        try:
            fn(w, job)
        except jobs.JobStateError as e:
            raise ApiError(409, "invalid_state", str(e)) from e
        return job_out(job)


@router.post("/downloads/bot-check/retry-now", response_model=BotCheckRetryOut)
def bot_check_retry_now(user: User = Depends(current_user)) -> BotCheckRetryOut:
    """End a YouTube bot-check pause early. If YouTube still refuses, the queue pauses again for
    the same time as before (never longer because of a manual retry)."""
    with write_session() as s:
        resumed = bot_guard.retry_now(s, datetime.now(UTC))
        if resumed:
            s.add(AuditEvent(user_id=user.id, action="downloads.bot_check_retry", detail={}))
    if resumed:
        log.info("bot-check pause ended early by user %s", user.id)
        jobs.wake()
    return BotCheckRetryOut(resumed=resumed)


@router.post("/downloads/{job_id}/retry", response_model=JobOut)
def retry(job_id: int, user: User = Depends(current_user)) -> JobOut:
    return _action(job_id, user, jobs.retry_job)


@router.post("/downloads/{job_id}/cancel", response_model=JobOut)
def cancel(job_id: int, user: User = Depends(current_user)) -> JobOut:
    return _action(job_id, user, jobs.cancel_job)


@router.post("/downloads/{job_id}/pause", response_model=JobOut)
def pause(job_id: int, user: User = Depends(current_user)) -> JobOut:
    return _action(job_id, user, jobs.pause_job)


@router.post("/downloads/{job_id}/resume", response_model=JobOut)
def resume(job_id: int, user: User = Depends(current_user)) -> JobOut:
    return _action(job_id, user, jobs.resume_job)
