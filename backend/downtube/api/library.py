from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from downtube.api.views import PageParams, user_video_ids, video_states
from downtube.api.youtube_write import call_youtube, ensure_manage_permission
from downtube.auth.audit import audit
from downtube.auth.deps import current_user, request_db
from downtube.auth.ratelimit import client_ip
from downtube.db import read_session, write_session
from downtube.downloads import jobs
from downtube.downloads.manager import write_nfo
from downtube.main import ApiError
from downtube.models import (
    ACTIVE_JOB_STATUSES,
    AccountStatus,
    Channel,
    DiscoverySource,
    DownloadJob,
    GoogleAccount,
    JobStatus,
    MediaFile,
    User,
    Video,
    VideoExclusion,
    VideoSource,
)
from downtube.schemas import (
    DeleteVideoRequest,
    DeleteVideoResult,
    LibraryChannelOut,
    MediaFileOut,
    Page,
    ReleaseDateResult,
    SidecarResult,
)
from downtube.storage import paths
from downtube.sync.release_dates import backfill_release_dates
from downtube.youtube.client import YouTubeClient

log = logging.getLogger("downtube.api")
router = APIRouter(tags=["library"])

MEDIA_TYPES = {"mp4": "video/mp4", "mkv": "video/x-matroska", "webm": "video/webm"}


def media_path(mf: MediaFile) -> Path:
    """Absolute path of a recorded media file, verified to stay inside its root and the storage mounts."""
    return paths.media_file_path(mf.root_path, mf.rel_path)


def _thumb_path(mf: MediaFile) -> Path | None:
    try:
        thumb = media_path(mf).with_suffix(".jpg")
        return paths.resolve_within_mounts(thumb) if thumb.is_file() else None
    except (paths.StorageError, OSError):
        return None


def _current_for_user(db: Session, user: User, video_id: str) -> MediaFile:
    mf = db.scalar(
        select(MediaFile).where(
            MediaFile.video_id == video_id,
            MediaFile.is_current.is_(True),
            MediaFile.video_id.in_(user_video_ids(user.id)),
        )
    )
    if mf is None:
        raise ApiError(404, "not_found", "no archived file for this video")
    return mf


@router.get("/library/channels", response_model=list[LibraryChannelOut])
def library_channels(user: User = Depends(current_user), db: Session = Depends(request_db)) -> list[LibraryChannelOut]:
    """Every channel with at least one archived video in the user's library (for the channel filter)."""
    title = func.coalesce(func.max(Channel.title), func.max(Video.channel_title), Video.channel_id)
    rows = db.execute(
        select(Video.channel_id, title, func.count(MediaFile.id))
        .join(MediaFile, MediaFile.video_id == Video.id)
        .outerjoin(Channel, Channel.id == Video.channel_id)
        .where(
            MediaFile.is_current.is_(True),
            MediaFile.video_id.in_(user_video_ids(user.id)),
            Video.channel_id.is_not(None),
        )
        .group_by(Video.channel_id)
    ).all()
    out = [LibraryChannelOut(channel_id=cid, title=t or cid, count=n) for cid, t, n in rows]
    return sorted(out, key=lambda c: (c.title.casefold(), c.channel_id))


@router.get("/library", response_model=Page[MediaFileOut])
def library(
    q: str | None = None,
    channel_id: str | None = None,
    # "date" is the pre-0.5 name of "published" (release date), kept for compatibility.
    sort: Literal["published", "downloaded", "date", "title", "size", "height"] = "published",
    order: Literal["asc", "desc"] = "desc",
    pp: PageParams = Depends(),
    user: User = Depends(current_user),
    db: Session = Depends(request_db),
) -> Page[MediaFileOut]:
    base = (
        select(MediaFile, Video)
        .join(Video, Video.id == MediaFile.video_id)
        .where(MediaFile.is_current.is_(True), MediaFile.video_id.in_(user_video_ids(user.id)))
    )
    if q:
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        base = base.where(
            Video.title.ilike(f"%{esc}%", escape="\\") | Video.channel_title.ilike(f"%{esc}%", escape="\\")
        )
    if channel_id:
        base = base.where(Video.channel_id == channel_id)
    key = {
        "published": Video.published_at,
        "date": Video.published_at,
        "downloaded": MediaFile.created_at,
        "title": func.lower(Video.title),
        "size": MediaFile.size_bytes,
        "height": func.min(MediaFile.width, MediaFile.height),  # short side (vertical videos)
    }[sort]
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.execute(
        # Unknown values (e.g. a release date not fetched yet) always go last.
        base.order_by(key.is_(None), key.desc() if order == "desc" else key.asc(), MediaFile.id)
        .offset(pp.offset)
        .limit(pp.page_size)
    ).all()
    states = video_states(db, user.id, [mf.video_id for mf, _ in rows])
    items = []
    for mf, v in rows:
        thumb = f"/api/library/{v.id}/thumbnail" if _thumb_path(mf) else v.thumbnail_url
        items.append(
            MediaFileOut(
                id=mf.id,
                video_id=v.id,
                title=v.title,
                channel_id=v.channel_id,
                channel_title=v.channel_title,
                thumbnail_url=thumb,
                url=v.url,
                rel_path=mf.rel_path,
                container=mf.container,
                width=mf.width,
                height=mf.height,
                fps=mf.fps,
                vcodec=mf.vcodec,
                acodec=mf.acodec,
                duration_s=mf.duration_s,
                size_bytes=mf.size_bytes,
                is_hdr=mf.is_hdr,
                source_format_id=mf.source_format_id,
                max_height_requested=mf.max_height_requested,
                created_at=mf.created_at,
                published_at=v.published_at,
                sources=states[v.id].sources,  # type: ignore[arg-type]
            )
        )
    return Page[MediaFileOut](items=items, total=total, page=pp.page, page_size=pp.page_size)


@router.get("/library/{video_id}/stream")
def stream(video_id: str, user: User = Depends(current_user), db: Session = Depends(request_db)) -> FileResponse:
    mf = _current_for_user(db, user, video_id)
    try:
        path = media_path(mf)
    except paths.StorageError as e:
        log.warning("refusing to stream %s: %s", video_id, e)
        raise ApiError(404, "not_found", "file is not available") from e
    if not path.is_file() or not os.access(path, os.R_OK):
        raise ApiError(404, "file_missing", "the archived file is missing on disk")
    return FileResponse(path, media_type=MEDIA_TYPES.get(mf.container, "application/octet-stream"))


@router.get("/library/{video_id}/thumbnail")
def thumbnail(video_id: str, user: User = Depends(current_user), db: Session = Depends(request_db)) -> FileResponse:
    thumb = _thumb_path(_current_for_user(db, user, video_id))
    if thumb is None:
        raise ApiError(404, "not_found", "no local thumbnail")
    return FileResponse(thumb, media_type="image/jpeg")


# --------------------------------------------------------------------------- delete


def _files_of(mf: MediaFile) -> list[Path]:
    """Files on disk that belong to a media row: the video, its sidecars, a kept superseded copy."""
    out: list[Path] = []
    try:
        media = media_path(mf)
    except paths.StorageError:
        return out
    if mf.is_current:
        out.append(media)
    if (mf.superseded_action or "").startswith("kept:"):
        out.append(media.with_name(mf.superseded_action.split(":", 1)[1]))  # type: ignore[union-attr]
    out += [media.with_name(media.stem + ".jpg"), media.with_name(media.stem + ".nfo")]
    return out


def _rel(root: str, p: Path) -> str:
    try:
        return str(p.relative_to(paths.resolve_within_mounts(root)))
    except (ValueError, paths.StorageError):
        return p.name


@router.post("/library/{video_id}/delete", response_model=DeleteVideoResult)
def delete_video(
    video_id: str,
    request: Request,
    body: DeleteVideoRequest | None = None,
    user: User = Depends(current_user),
) -> DeleteVideoResult:
    """Delete an archived video. Liked → optionally remove the like on YouTube; in every case the
    video is excluded so no sync downloads it again for this user."""
    body = body or DeleteVideoRequest()
    with read_session() as s:
        current = _current_for_user(s, user, video_id)
        if s.scalar(
            select(DownloadJob.id).where(DownloadJob.video_id == video_id, DownloadJob.status == JobStatus.running)
        ):
            raise ApiError(409, "job_running", "a download for this video is running; cancel it in Downloads first")
        others = s.scalar(
            select(func.count())
            .select_from(VideoSource)
            .where(
                VideoSource.video_id == video_id,
                VideoSource.user_id != user.id,
                VideoSource.active.is_(True),
                VideoSource.archive_eligible.is_(True),
            )
        )
        if others:
            raise ApiError(409, "shared_video", "another DownTube user also archives this video; it was not deleted")
        liked = (
            s.scalar(
                select(VideoSource.id).where(
                    VideoSource.user_id == user.id,
                    VideoSource.video_id == video_id,
                    VideoSource.source == DiscoverySource.like,
                    VideoSource.active.is_(True),
                )
            )
            is not None
        )
        root = current.root_path

    like_removed = False
    if liked and body.remove_like:
        ensure_manage_permission(user.id)  # fail before anything is deleted
        call_youtube(user.id, lambda yt: yt.rate(video_id, "none"))
        like_removed = True

    deleted: list[str] = []
    warnings: list[str] = []
    with write_session() as w:
        rows = list(w.scalars(select(MediaFile).where(MediaFile.video_id == video_id)))
        files: list[Path] = []
        main: Path | None = None
        for mf in rows:
            if mf.is_current:
                try:
                    main = media_path(mf)
                except paths.StorageError:
                    main = None
            for f in _files_of(mf):
                if f not in files:
                    files.append(f)
        for f in files:
            try:
                safe = paths.resolve_within_mounts(f)
                if safe.is_file():
                    safe.unlink()
                    deleted.append(_rel(root, safe))
            except paths.StorageError:
                warnings.append(f"skipped a file outside the archive: {f.name}")
            except OSError as e:
                if f == main:  # the video itself must go, or nothing changes
                    raise ApiError(500, "storage_error", f"could not delete the file: {e.strerror or e}") from e
                warnings.append(f"could not delete {f.name}: {e.strerror or e}")
        now = datetime.now(UTC)
        for mf in rows:
            if mf.is_current or (mf.superseded_action or "").startswith("kept:"):
                mf.is_current = False
                mf.superseded_at = mf.superseded_at or now
                mf.superseded_action = "deleted_by_user"
        for job in w.scalars(
            select(DownloadJob).where(DownloadJob.video_id == video_id, DownloadJob.status.in_(ACTIVE_JOB_STATUSES))
        ):
            jobs.cancel_job(w, job)
        if w.get(VideoExclusion, (user.id, video_id)) is None:
            w.add(VideoExclusion(user_id=user.id, video_id=video_id, reason="deleted"))
        if like_removed:
            for src in w.scalars(
                select(VideoSource).where(
                    VideoSource.user_id == user.id,
                    VideoSource.video_id == video_id,
                    VideoSource.source == DiscoverySource.like,
                )
            ):
                src.active = False
        if liked and not body.remove_like:
            warnings.append("the video is still liked on YouTube")
        audit(
            w,
            "library.delete",
            user.id,
            {"video_id": video_id, "files": len(deleted), "like_removed": like_removed},
            client_ip(request),
        )
    log.info(
        "user %s deleted %s from the library (%d files, like removed: %s)",
        user.id,
        video_id,
        len(deleted),
        like_removed,
    )
    return DeleteVideoResult(
        video_id=video_id, deleted_files=deleted, like_removed=like_removed, excluded=True, warnings=warnings
    )


# --------------------------------------------------------------------------- sidecars


@router.post("/library/release-dates", response_model=ReleaseDateResult)
def fetch_release_dates(user: User = Depends(current_user)) -> ReleaseDateResult:
    """Look up missing release dates now (YouTube API, then the date embedded in archived files)."""
    with read_session() as s:
        account_id = s.scalar(
            select(GoogleAccount.id)
            .where(GoogleAccount.user_id == user.id, GoogleAccount.status == AccountStatus.active)
            .order_by(GoogleAccount.id)
        )
    if account_id is None:
        return backfill_release_dates()
    with YouTubeClient(account_id) as yt:
        return backfill_release_dates(client=yt)


@router.post("/library/sidecars", response_model=SidecarResult)
def write_sidecars(overwrite: bool = False, user: User = Depends(current_user)) -> SidecarResult:
    """Write the description (.nfo) next to every archived video of this user that lacks one."""
    written = skipped = failed = 0
    with read_session() as s:
        rows = s.execute(
            select(MediaFile, Video, Channel.title)
            .join(Video, Video.id == MediaFile.video_id)
            .outerjoin(Channel, Channel.id == Video.channel_id)
            .where(MediaFile.is_current.is_(True), MediaFile.video_id.in_(user_video_ids(user.id)))
        ).all()
        work: list[tuple[MediaFile, Video, str | None]] = []
        for mf, v, ch_title in rows:
            snap = Video(
                id=v.id, title=v.title, description=v.description, published_at=v.published_at, duration_s=v.duration_s
            )
            work.append((mf, snap, ch_title or v.channel_title))
        for row in work:
            s.expunge(row[0])
    for mf, snap, title in work:
        try:
            media = media_path(mf)
            if not media.is_file():
                skipped += 1
                continue
            if media.with_name(media.stem + ".nfo").exists() and not overwrite:
                skipped += 1
                continue
            write_nfo(media, snap, title)
            written += 1
        except (paths.StorageError, OSError) as e:
            failed += 1
            log.warning("could not write .nfo for %s: %s", mf.video_id, e)
    return SidecarResult(written=written, skipped=skipped, failed=failed)
