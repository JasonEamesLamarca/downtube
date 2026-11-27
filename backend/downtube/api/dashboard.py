from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from downtube.api.views import quota_info, storage_info, user_video_ids
from downtube.auth.deps import current_user, request_db
from downtube.downloads import bot_guard
from downtube.downloads.manager import utcnow
from downtube.models import (
    AccountStatus,
    ArchiveMode,
    ChannelRule,
    DiscoverySource,
    DownloadJob,
    GoogleAccount,
    JobStatus,
    MediaFile,
    SyncRun,
    SyncStatus,
    User,
    VideoSource,
)
from downtube.schemas import Dashboard, SyncRunOut
from downtube.settings_service import get_value

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=Dashboard)
def dashboard(user: User = Depends(current_user), db: Session = Depends(request_db)) -> Dashboard:
    mine = user_video_ids(user.id)
    acc = db.scalar(select(GoogleAccount).where(GoogleAccount.user_id == user.id))
    archived, size = db.execute(
        select(func.count(MediaFile.id), func.coalesce(func.sum(MediaFile.size_bytes), 0)).where(
            MediaFile.is_current.is_(True), MediaFile.video_id.in_(mine)
        )
    ).one()
    monitored = db.scalar(
        select(func.count(ChannelRule.id)).where(
            ChannelRule.user_id == user.id, ChannelRule.mode != ArchiveMode.off, ChannelRule.paused.is_(False)
        )
    )
    liked = db.scalar(
        select(func.count(VideoSource.id)).where(
            VideoSource.user_id == user.id, VideoSource.source == DiscoverySource.like, VideoSource.active.is_(True)
        )
    )

    def jobs_with(*statuses: JobStatus) -> int:
        return (
            db.scalar(
                select(func.count(DownloadJob.id)).where(
                    DownloadJob.status.in_(statuses), DownloadJob.video_id.in_(mine)
                )
            )
            or 0
        )

    speed = db.scalar(
        select(func.coalesce(func.sum(DownloadJob.speed_bps), 0.0)).where(
            DownloadJob.status == JobStatus.running, DownloadJob.video_id.in_(mine)
        )
    )
    last_likes = db.scalar(
        select(func.max(SyncRun.finished_at)).where(
            SyncRun.user_id == user.id, SyncRun.kind.in_(("likes", "likes_full")), SyncRun.status == SyncStatus.ok
        )
    )
    paused_until, bot_checks = bot_guard.status(db, utcnow())
    recent = db.scalars(select(SyncRun).where(SyncRun.user_id == user.id).order_by(SyncRun.id.desc()).limit(10)).all()
    return Dashboard(
        account_email=acc.email if acc else user.email,
        account_status=acc.status.value if acc else AccountStatus.disconnected.value,
        archived_videos=archived or 0,
        archive_bytes=int(size or 0),
        monitored_channels=monitored or 0,
        liked_videos_discovered=liked or 0,
        downloads_active=jobs_with(JobStatus.running),
        downloads_pending=jobs_with(JobStatus.queued, JobStatus.paused),
        downloads_failed=jobs_with(JobStatus.failed),
        current_speed_bps=float(speed or 0.0),
        downloads_paused_until=paused_until,
        bot_checks_in_a_row=bot_checks,
        storage=storage_info(db),
        quota=quota_info(),
        likes_mode=get_value(db, "likes_mode"),
        last_likes_sync=last_likes,
        recent_syncs=[SyncRunOut.model_validate(r) for r in recent],
    )
