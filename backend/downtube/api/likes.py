from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from downtube.api.views import PageParams, video_out, video_states
from downtube.auth.deps import current_user, request_db, require_acknowledged
from downtube.db import write_session
from downtube.downloads import jobs
from downtube.main import ApiError
from downtube.models import DiscoverySource, User, Video, VideoSource
from downtube.schemas import LikesBackfillRequest, LikesSyncRequest, Page, SyncRunOut, VideoOut
from downtube.settings_service import get_value
from downtube.sync import common, runner

router = APIRouter(tags=["likes"])

StateFilter = Literal["not_archived", "queued", "downloading", "archived", "failed", "unsupported", "excluded"]


@router.get("/likes", response_model=Page[VideoOut])
def list_likes(
    q: str | None = None,
    state: StateFilter | None = None,
    pp: PageParams = Depends(),
    user: User = Depends(current_user),
    db: Session = Depends(request_db),
) -> Page[VideoOut]:
    base = (
        select(Video, VideoSource.discovered_at)
        .join(VideoSource, VideoSource.video_id == Video.id)
        .where(
            VideoSource.user_id == user.id,
            VideoSource.source == DiscoverySource.like,
            VideoSource.active.is_(True),
        )
    )
    if q:
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{esc}%"
        base = base.where(Video.title.ilike(like, escape="\\") | Video.channel_title.ilike(like, escape="\\"))
    base = base.order_by(VideoSource.discovered_at.desc(), Video.id)
    if state is None:
        total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = db.execute(base.offset(pp.offset).limit(pp.page_size)).all()
        states = video_states(db, user.id, [v.id for v, _ in rows])
        items = [video_out(v, states[v.id]) for v, _ in rows]
    else:  # archive_state is derived, so filter in Python (likes are bounded by the API's 1000 cap)
        rows = db.execute(base).all()
        states = video_states(db, user.id, [v.id for v, _ in rows])
        matching = [video_out(v, states[v.id]) for v, _ in rows if states[v.id].state == state]
        total = len(matching)
        items = matching[pp.offset : pp.offset + pp.page_size]
    return Page[VideoOut](items=items, total=total, page=pp.page, page_size=pp.page_size)


@router.post("/likes/sync", response_model=SyncRunOut, status_code=202)
def sync_likes(body: LikesSyncRequest | None = None, user: User = Depends(current_user)) -> SyncRunOut:
    full = bool(body and body.full)
    return SyncRunOut.model_validate(runner.start("likes_full" if full else "likes", user.id))


@router.post("/likes/backfill")
def backfill(body: LikesBackfillRequest | None = None, user: User = Depends(require_acknowledged)) -> dict:
    """Make baseline (future_only) likes archivable and queue them."""
    confirm = bool(body and body.confirm)
    queued = 0
    with write_session() as w:
        if get_value(w, "likes_mode") == "off":
            raise ApiError(409, "likes_disabled", "likes archiving is off; enable it in settings first")
        pending = list(
            w.scalars(
                select(VideoSource).where(
                    VideoSource.user_id == user.id,
                    VideoSource.source == DiscoverySource.like,
                    VideoSource.active.is_(True),
                    VideoSource.archive_eligible.is_(False),
                )
            )
        )
        threshold = int(get_value(w, "large_channel_threshold"))
        if len(pending) > threshold and not confirm:
            raise ApiError(
                409,
                "confirmation_required",
                f"{len(pending)} liked videos will be queued; confirm to continue",
                {"count": len(pending), "threshold": threshold},
            )
        for src in pending:
            src.archive_eligible = True
        w.flush()
        for src in pending:
            if common.enqueue_new(w, src.video_id, user.id, jobs.PRIORITY_BACKFILL):
                queued += 1
    jobs.wake()
    return {"queued": queued}
