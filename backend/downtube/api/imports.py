"""Manual imports: upload a file for a download, or pick up files from the import folder.

See ``downloads/importer.py`` for the checks. DownTube never receives YouTube cookies: the user
downloads the file on their own computer and hands it over.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from downtube.api.views import job_out, user_video_ids
from downtube.auth.deps import current_user, require_acknowledged
from downtube.db import read_session
from downtube.downloads import importer
from downtube.main import ApiError
from downtube.models import DownloadJob, User
from downtube.schemas import ImportFileOut, ImportFolderOut, JobOut
from downtube.settings_service import get_settings
from downtube.storage import paths

log = logging.getLogger("downtube.api")
router = APIRouter(tags=["imports"])


def _owned_job(job_id: int, user: User) -> str:
    with read_session() as s:
        video_id = s.scalar(
            select(DownloadJob.video_id).where(
                DownloadJob.id == job_id, DownloadJob.video_id.in_(user_video_ids(user.id))
            )
        )
    if video_id is None:
        raise ApiError(404, "not_found", "download not found")
    return video_id


def _suffix(filename: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    return suffix if suffix in importer.MEDIA_SUFFIXES else ".mp4"


@router.put("/downloads/{job_id}/import", response_model=JobOut)
async def upload_import(
    job_id: int,
    request: Request,
    filename: str | None = Query(None, max_length=500),
    user: User = Depends(require_acknowledged),
) -> JobOut:
    """Upload a file the user downloaded themselves; the raw request body is the file."""
    video_id = _owned_job(job_id, user)
    named = importer.video_id_from_name(filename or "")
    if named and named != video_id:
        raise ApiError(422, "wrong_video", f"the file name says it is video {named}, not {video_id}")

    root = await run_in_threadpool(importer.archive_root)
    folder = await run_in_threadpool(importer.upload_dir, root)
    length = request.headers.get("content-length")
    with read_session() as s:
        min_free = get_settings(s).min_free_gb * 1024**3
    if length and length.isdigit() and not paths.has_space_for(root, int(length), min_free):
        raise ApiError(507, "disk_space", "not enough free space in the archive for this file")

    dest = folder / f"{job_id}-{uuid.uuid4().hex}{_suffix(filename)}"
    size = 0
    try:
        with open(dest, "wb") as fh:
            async for chunk in request.stream():
                if chunk:
                    await run_in_threadpool(fh.write, chunk)
                    size += len(chunk)
        if size == 0:
            raise ApiError(422, "empty_upload", "the uploaded file is empty")
        await run_in_threadpool(importer.import_file, dest, video_id, origin="upload", user_id=user.id, job_id=job_id)
    except ClientDisconnect:
        log.info("upload for job %s aborted by the client", job_id)
        raise ApiError(400, "upload_aborted", "the upload was interrupted") from None
    except importer.ImportFailed as e:
        raise ApiError(e.status, e.code, e.message) from e
    finally:
        dest.unlink(missing_ok=True)  # gone already when the import succeeded

    with read_session() as s:
        job = s.get(DownloadJob, job_id)
        assert job is not None
        return job_out(job)


def _folder_out(files: list[importer.FolderFile]) -> ImportFolderOut:
    return ImportFolderOut(
        path=importer.host_import_path(),
        files=[
            ImportFileOut(name=f.name, size_bytes=f.size_bytes, video_id=f.video_id, status=f.status, message=f.message)  # type: ignore[arg-type]
            for f in files
        ],
    )


@router.get("/imports", response_model=ImportFolderOut)
def import_folder(_: User = Depends(current_user)) -> ImportFolderOut:
    try:
        return _folder_out(importer.list_folder())
    except (OSError, paths.StorageError) as e:
        raise ApiError(503, "storage", f"import folder not available: {e}") from e


@router.post("/imports/scan", response_model=ImportFolderOut)
def scan_import_folder(_: User = Depends(require_acknowledged)) -> ImportFolderOut:
    try:
        return _folder_out(importer.scan_folder(force=True))
    except (OSError, paths.StorageError) as e:
        raise ApiError(503, "storage", f"import folder not available: {e}") from e
