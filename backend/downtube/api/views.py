"""Shared query/serialisation helpers for the API routers (everything is scoped to a user)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from fastapi import Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from downtube.config import get_config
from downtube.models import DownloadJob, JobStatus, MediaFile, Video, VideoExclusion, VideoSource
from downtube.schemas import JobOut, QuotaInfo, StorageInfo, VideoOut
from downtube.settings_service import get_settings
from downtube.storage import paths


class PageParams:
    def __init__(self, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def user_video_ids(user_id: int):  # type: ignore[no-untyped-def]
    """Subquery: ids of videos the user has any source for (library / job visibility)."""
    return select(VideoSource.video_id).where(VideoSource.user_id == user_id)


@dataclass
class VideoState:
    state: str = "not_archived"
    job_id: int | None = None
    archived_height: int | None = None
    best_available_height: int | None = None
    sources: list[str] = field(default_factory=list)
    discovered_at: datetime | None = None


def video_states(s: Session, user_id: int, video_ids: list[str]) -> dict[str, VideoState]:
    out = {v: VideoState() for v in video_ids}
    if not video_ids:
        return out
    media = {
        m.video_id: m
        for m in s.scalars(select(MediaFile).where(MediaFile.video_id.in_(video_ids), MediaFile.is_current.is_(True)))
    }
    latest: dict[str, DownloadJob] = {}
    active: dict[str, DownloadJob] = {}
    for j in s.scalars(select(DownloadJob).where(DownloadJob.video_id.in_(video_ids)).order_by(DownloadJob.id)):
        latest[j.video_id] = j
        if j.status in (JobStatus.queued, JobStatus.running, JobStatus.paused):
            active[j.video_id] = j
    srcs: dict[str, list[VideoSource]] = {}
    for src in s.scalars(
        select(VideoSource).where(VideoSource.user_id == user_id, VideoSource.video_id.in_(video_ids))
    ):
        srcs.setdefault(src.video_id, []).append(src)
    excluded = set(
        s.scalars(
            select(VideoExclusion.video_id).where(
                VideoExclusion.user_id == user_id, VideoExclusion.video_id.in_(video_ids)
            )
        )
    )
    for vid, st in out.items():
        mine = srcs.get(vid, [])
        live = [x for x in mine if x.active]
        st.sources = sorted({x.source.value for x in (live or mine)})
        dates = [x.discovered_at for x in mine]
        st.discovered_at = min(dates) if dates else None
        mf = media.get(vid)
        st.archived_height = mf.height if mf else None
        st.best_available_height = mf.best_available_height if mf else None
        if vid in active:
            st.job_id = active[vid].id
        elif vid in latest:
            st.job_id = latest[vid].id
        if mf is not None:
            st.state = "archived"
        elif vid in active:
            st.state = "downloading" if active[vid].status == JobStatus.running else "queued"
        elif vid in excluded:
            st.state = "excluded"
        elif vid in latest and latest[vid].status == JobStatus.failed:
            st.state = "failed"
        elif vid in latest and latest[vid].status == JobStatus.unsupported:
            st.state = "unsupported"
        elif not any(x.active and x.archive_eligible for x in mine):
            st.state = "excluded"
    return out


def video_out(v: Video, st: VideoState) -> VideoOut:
    return VideoOut(
        video_id=v.id,
        title=v.title,
        channel_id=v.channel_id,
        channel_title=v.channel_title,
        thumbnail_url=v.thumbnail_url,
        url=v.url,
        published_at=v.published_at,
        duration_s=v.duration_s,
        is_short=v.is_short,
        is_live=v.is_live,
        availability=v.availability.value,
        sources=st.sources,  # type: ignore[arg-type]
        discovered_at=st.discovered_at,
        archive_state=st.state,  # type: ignore[arg-type]
        best_available_height=st.best_available_height,
        archived_height=st.archived_height,
        job_id=st.job_id,
    )


def job_out(j: DownloadJob) -> JobOut:
    v = j.video
    return JobOut(
        id=j.id,
        video_id=j.video_id,
        title=v.title if v else None,
        channel_title=v.channel_title if v else None,
        thumbnail_url=v.thumbnail_url if v else None,
        kind=j.kind.value,  # type: ignore[arg-type]
        priority=j.priority,
        status=j.status.value,  # type: ignore[arg-type]
        stage=j.stage.value,  # type: ignore[arg-type]
        max_height=j.max_height,
        attempts=j.attempts,
        max_attempts=j.max_attempts,
        next_attempt_at=j.next_attempt_at,
        progress_pct=j.progress_pct,
        bytes_downloaded=j.bytes_downloaded,
        bytes_total=j.bytes_total,
        speed_bps=j.speed_bps,
        eta_s=j.eta_s,
        error_code=j.error_code,
        error_message=j.error_message,
        selection=j.selection,
        created_at=j.created_at,
        started_at=j.started_at,
        finished_at=j.finished_at,
    )


def storage_info(s: Session) -> StorageInfo:
    from sqlalchemy import func

    st = get_settings(s)
    root = st.archive_root
    total = free = 0
    writable = False
    try:
        real = paths.resolve_within_mounts(root)
        if real.exists():
            total, free = paths.disk_usage(real)
        writable = paths.is_writable(real)
    except (paths.StorageError, OSError):
        pass
    used = s.scalar(select(func.coalesce(func.sum(MediaFile.size_bytes), 0)).where(MediaFile.is_current.is_(True))) or 0
    min_free = st.min_free_gb * 1024**3
    return StorageInfo(
        root_path=root,
        host_path_hint=get_config().host_archive_path,
        total_bytes=total,
        free_bytes=free,
        archive_bytes=int(used),
        min_free_bytes=min_free,
        below_threshold=bool(total) and free < min_free,
        writable=writable,
    )


def quota_info() -> QuotaInfo:
    from downtube.youtube import quota

    return quota.info()
