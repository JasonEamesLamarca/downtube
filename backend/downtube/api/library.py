from __future__ import annotations

import logging
import os
from pathlib import Path, PurePosixPath
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from downtube.api.views import PageParams, user_video_ids, video_states
from downtube.auth.deps import current_user, request_db
from downtube.main import ApiError
from downtube.models import MediaFile, User, Video
from downtube.schemas import MediaFileOut, Page
from downtube.storage import paths

log = logging.getLogger("downtube.api")
router = APIRouter(tags=["library"])

MEDIA_TYPES = {"mp4": "video/mp4", "mkv": "video/x-matroska", "webm": "video/webm"}


def media_path(mf: MediaFile) -> Path:
    """Absolute path of a recorded media file, verified to stay inside its root and the storage mounts."""
    rel = PurePosixPath(mf.rel_path)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        raise paths.StorageError("invalid recorded path")
    root = paths.resolve_within_mounts(mf.root_path)
    return paths.resolve_within_mounts(paths.safe_join(root, *rel.parts))


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


@router.get("/library", response_model=Page[MediaFileOut])
def library(
    q: str | None = None,
    channel_id: str | None = None,
    sort: Literal["date", "title", "size", "height"] = "date",
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
        "date": Video.published_at,
        "title": func.lower(Video.title),
        "size": MediaFile.size_bytes,
        "height": MediaFile.height,
    }[sort]
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.execute(
        base.order_by(key.desc() if order == "desc" else key.asc(), MediaFile.id).offset(pp.offset).limit(pp.page_size)
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
