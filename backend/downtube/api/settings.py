"""Settings, storage, quota, sync history and log endpoints."""

from __future__ import annotations

import logging
from collections import deque
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from downtube import scheduler
from downtube.api.views import PageParams, quota_info, storage_info
from downtube.auth.audit import audit
from downtube.auth.deps import current_user, ensure_acknowledged, request_db, require_admin
from downtube.auth.ratelimit import client_ip
from downtube.config import get_config
from downtube.db import write_session
from downtube.downloads import jobs
from downtube.logging_setup import LOG_FILE, redact
from downtube.main import ApiError
from downtube.models import SyncRun, User
from downtube.schemas import (
    BrowseResult,
    LogLines,
    Page,
    QuotaInfo,
    SetRootRequest,
    SettingsOut,
    SettingsUpdate,
    StorageInfo,
    SyncRunOut,
)
from downtube.settings_service import SettingsError, get_settings, get_value, update_settings
from downtube.storage import paths

log = logging.getLogger("downtube.api")
router = APIRouter(tags=["settings"])


@router.get("/settings", response_model=SettingsOut)
def read_settings(user: User = Depends(current_user), db: Session = Depends(request_db)) -> SettingsOut:
    return get_settings(db)


def _checked_root(path: str, create: bool) -> Path:
    try:
        real = paths.resolve_within_mounts(path)
    except paths.StorageError as e:
        raise ApiError(400, "invalid_path", str(e)) from e
    if not real.exists() and create:
        try:
            real.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ApiError(400, "not_writable", f"cannot create directory: {e.strerror}") from e
    if not real.is_dir():
        raise ApiError(400, "invalid_path", "directory does not exist")
    if not paths.is_writable(real):
        raise ApiError(400, "not_writable", "directory is not writable by DownTube")
    return real


@router.put("/settings", response_model=SettingsOut)
def write_settings(body: SettingsUpdate, user: User = Depends(require_admin)) -> SettingsOut:
    with write_session() as w:
        if body.likes_mode is not None and body.likes_mode != "off" and get_value(w, "likes_mode") != body.likes_mode:
            ensure_acknowledged(user)
        if body.archive_root is not None:
            body.archive_root = str(_checked_root(body.archive_root, create=False))
        try:
            out = update_settings(w, body, user.id)
        except (SettingsError, ValueError) as e:
            raise ApiError(422, "invalid_setting", str(e)) from e
    scheduler.reschedule()
    jobs.wake()
    return out


@router.get("/storage", response_model=StorageInfo)
def storage(user: User = Depends(current_user), db: Session = Depends(request_db)) -> StorageInfo:
    return storage_info(db)


@router.get("/storage/browse", response_model=BrowseResult)
def browse(path: str | None = None, user: User = Depends(require_admin)) -> BrowseResult:
    try:
        return paths.browse(path)
    except paths.StorageError as e:
        raise ApiError(400, "invalid_path", str(e)) from e


@router.post("/storage/root", response_model=StorageInfo)
def set_root(body: SetRootRequest, request: Request, user: User = Depends(require_admin)) -> StorageInfo:
    """Change the archive root for *new* downloads. Existing archives are never moved."""
    real = _checked_root(body.path, body.create)
    with write_session() as w:
        update_settings(w, SettingsUpdate(archive_root=str(real)), user.id)
        audit(w, "storage.root", user.id, {"archive_root": str(real)}, client_ip(request))
        info = storage_info(w)
    jobs.wake()
    return info


@router.get("/sync/runs", response_model=Page[SyncRunOut])
def sync_runs(
    kind: str | None = None,
    pp: PageParams = Depends(),
    user: User = Depends(current_user),
    db: Session = Depends(request_db),
) -> Page[SyncRunOut]:
    base = select(SyncRun).where(SyncRun.user_id == user.id)
    if kind:
        base = base.where(SyncRun.kind == kind)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(base.order_by(SyncRun.id.desc()).offset(pp.offset).limit(pp.page_size)).all()
    return Page[SyncRunOut](
        items=[SyncRunOut.model_validate(r) for r in rows], total=total, page=pp.page, page_size=pp.page_size
    )


@router.get("/quota", response_model=QuotaInfo)
def quota(user: User = Depends(current_user)) -> QuotaInfo:
    return quota_info()


@router.get("/logs", response_model=LogLines)
def logs(lines: int = Query(200, ge=1, le=2000), user: User = Depends(require_admin)) -> LogLines:
    path = get_config().log_dir / LOG_FILE
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            tail = deque(f, maxlen=lines)
    except OSError:
        return LogLines(lines=[])
    return LogLines(lines=[redact(line.rstrip("\n")) for line in tail])
