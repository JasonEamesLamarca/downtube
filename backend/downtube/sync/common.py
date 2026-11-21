"""Shared helpers for the sync engines (run bookkeeping, upserts, checkpoints)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from downtube.db import read_session, write_session
from downtube.downloads import jobs
from downtube.models import (
    Availability,
    Channel,
    DiscoverySource,
    SyncCheckpoint,
    SyncRun,
    SyncStatus,
    User,
    Video,
    VideoSource,
)
from downtube.youtube.parse import VideoData

log = logging.getLogger("downtube.sync")


def now() -> datetime:
    return datetime.now(UTC)


def create_run(kind: str, user_id: int | None, target: str | None, trigger: str) -> int:
    with write_session() as s:
        run = SyncRun(user_id=user_id, kind=kind, target=target, trigger=trigger, status=SyncStatus.running)
        s.add(run)
        s.flush()
        return run.id


def finish_run(
    run_id: int,
    status: SyncStatus,
    *,
    items_seen: int = 0,
    items_new: int = 0,
    jobs_queued: int = 0,
    quota_used: int = 0,
    error: str | None = None,
) -> SyncRun:
    with write_session() as s:
        run = s.get(SyncRun, run_id)
        assert run is not None
        run.status = status
        run.finished_at = now()
        run.items_seen, run.items_new, run.jobs_queued, run.quota_used = items_seen, items_new, jobs_queued, quota_used
        run.error = error[:1000] if error else None
        return run


def get_run(run_id: int) -> SyncRun:
    with read_session() as s:
        run = s.get(SyncRun, run_id)
        assert run is not None
        s.expunge(run)  # read_session rolls back (expiring attached objects) on exit
        return run


def get_checkpoint(s: Session, user_id: int, kind: str, key: str = "") -> dict | None:
    row = s.scalar(
        select(SyncCheckpoint).where(
            SyncCheckpoint.user_id == user_id, SyncCheckpoint.kind == kind, SyncCheckpoint.key == key
        )
    )
    return dict(row.value) if row else None


def set_checkpoint(s: Session, user_id: int, kind: str, value: dict, key: str = "") -> None:
    row = s.scalar(
        select(SyncCheckpoint).where(
            SyncCheckpoint.user_id == user_id, SyncCheckpoint.kind == kind, SyncCheckpoint.key == key
        )
    )
    if row is None:
        s.add(SyncCheckpoint(user_id=user_id, kind=kind, key=key, value=value))
    else:
        row.value = value


def user_can_archive(s: Session, user_id: int) -> bool:
    user = s.get(User, user_id)
    return bool(user and user.acknowledged_terms_at is not None)


def upsert_channel_stub(s: Session, channel_id: str, title: str | None) -> Channel:
    ch = s.get(Channel, channel_id)
    if ch is None:
        ch = Channel(id=channel_id, title=title or channel_id)
        s.add(ch)
        s.flush()
    elif title and not ch.metadata_fetched_at:
        ch.title = title
    return ch


def upsert_video(s: Session, data: VideoData) -> Video:
    """Create or refresh the canonical Video row from API data."""
    if data.channel_id:
        upsert_channel_stub(s, data.channel_id, data.channel_title)
    v = s.get(Video, data.id)
    if v is None:
        v = Video(id=data.id)
        s.add(v)
    v.channel_id = data.channel_id or v.channel_id
    v.channel_title = data.channel_title or v.channel_title
    v.title = data.title or v.title
    v.description = data.description
    v.published_at = data.published_at or v.published_at
    v.duration_s = data.duration_s
    v.is_short = data.is_short
    v.is_live = data.is_live
    v.live_status = data.live_status
    v.availability = data.availability
    v.thumbnail_url = data.thumbnail_url or v.thumbnail_url
    v.metadata_fetched_at = now()
    s.flush()
    return v


def mark_unavailable(s: Session, video_id: str, availability: Availability = Availability.deleted) -> None:
    v = s.get(Video, video_id)
    if v is not None:
        if v.availability not in (Availability.private, Availability.deleted):
            v.availability = availability
        v.metadata_fetched_at = now()


def get_source(s: Session, user_id: int, video_id: str, source: DiscoverySource) -> VideoSource | None:
    return s.scalar(
        select(VideoSource).where(
            VideoSource.user_id == user_id, VideoSource.video_id == video_id, VideoSource.source == source
        )
    )


def enqueue_new(s: Session, video_id: str, user_id: int, priority: int) -> bool:
    """Enqueue; True only if a *new* job was created (not when merging into an existing one)."""
    existed = jobs.active_job(s, video_id) is not None
    job = jobs.enqueue(s, video_id, user_id=user_id, priority=priority)
    return job is not None and not existed
