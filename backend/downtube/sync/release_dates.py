"""Fill in missing release dates (``Video.published_at``) so the library can sort by them.

1. YouTube Data API ``videos.list`` (1 unit per 50 videos) for every video without a date that is
   not known to be private/deleted.
2. For archived videos still without a date (gone from YouTube, or no API access): the date tag
   yt-dlp embeds in the file (``--embed-metadata`` writes ``date=YYYYMMDD``).

Runs at startup, daily with the metadata job, and on demand from Settings.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from sqlalchemy import func, select

from downtube.auth.tokens import AuthRevoked
from downtube.config import get_config
from downtube.db import read_session, write_session
from downtube.downloads.validator import ValidationError, ffprobe_json
from downtube.models import AccountStatus, Availability, GoogleAccount, MediaFile, Video
from downtube.schemas import ReleaseDateResult
from downtube.storage import paths
from downtube.sync import common
from downtube.youtube.client import QuotaExceeded, YouTubeClient, YouTubeError
from downtube.youtube.parse import video_from_api

log = logging.getLogger("downtube.sync.release_dates")

_DATE_TAG = re.compile(r"^(\d{4})-?(\d{2})-?(\d{2})")


def parse_date_tag(value: str | None) -> datetime | None:
    """``20240115`` / ``2024-01-15`` / ``2024-01-15T10:00:00Z`` → midnight UTC of that day."""
    m = _DATE_TAG.match((value or "").strip())
    if not m:
        return None
    try:
        d = datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=UTC)
    except ValueError:
        return None
    return d if 2005 <= d.year <= datetime.now(UTC).year + 1 else None  # YouTube started in 2005


def file_release_date(root_path: str, rel_path: str) -> datetime | None:
    """Release date embedded in an archived file's container tags, if any."""
    try:
        data = ffprobe_json(paths.media_file_path(root_path, rel_path), get_config().ffprobe_bin, timeout=30)
    except (paths.StorageError, ValidationError, OSError):
        return None
    tags = {str(k).lower(): str(v) for k, v in ((data.get("format") or {}).get("tags") or {}).items()}
    for key in ("date", "release_date", "upload_date"):
        found = parse_date_tag(tags.get(key))
        if found:
            return found
    return None


def _missing_ids() -> list[str]:
    with read_session() as s:
        return list(
            s.scalars(
                select(Video.id)
                .where(
                    Video.published_at.is_(None),
                    Video.availability.not_in((Availability.private, Availability.deleted)),
                )
                .order_by(Video.id)
            )
        )


def _from_api(ids: list[str], client: YouTubeClient | None) -> tuple[int, str | None]:
    if not ids:
        return 0, None
    yt = client
    if yt is None:
        with read_session() as s:
            account_id = s.scalar(
                select(GoogleAccount.id).where(GoogleAccount.status == AccountStatus.active).order_by(GoogleAccount.id)
            )
        if account_id is None:
            return 0, "no connected Google account"
        yt = YouTubeClient(account_id, budget_fraction=0.9)
    found = 0
    try:
        for i in range(0, len(ids), 50):
            chunk = ids[i : i + 50]
            items = {it["id"]: it for it in yt.videos(chunk)}
            with write_session() as s:
                for vid in chunk:
                    if vid in items:
                        v = common.upsert_video(s, video_from_api(items[vid]))
                        found += v.published_at is not None
                    else:
                        common.mark_unavailable(s, vid)
    except (QuotaExceeded, AuthRevoked, YouTubeError) as e:
        log.warning("release dates: YouTube lookup stopped: %s", e)
        return found, str(e)
    finally:
        if client is None:
            yt.close()
    return found, None


def _from_files() -> int:
    with read_session() as s:
        rows = s.execute(
            select(MediaFile.video_id, MediaFile.root_path, MediaFile.rel_path)
            .join(Video, Video.id == MediaFile.video_id)
            .where(MediaFile.is_current.is_(True), Video.published_at.is_(None))
        ).all()
    found = 0
    for vid, root, rel in rows:
        date = file_release_date(root, rel)
        if date is None:
            continue
        with write_session() as s:
            v = s.get(Video, vid)
            if v is not None and v.published_at is None:
                v.published_at = date
                found += 1
    return found


def backfill_release_dates(*, client: YouTubeClient | None = None) -> ReleaseDateResult:
    ids = _missing_ids()
    from_youtube, error = _from_api(ids, client)
    from_files = _from_files()
    with read_session() as s:
        archived_missing = (
            s.scalar(
                select(func.count(MediaFile.id))
                .join(Video, Video.id == MediaFile.video_id)
                .where(MediaFile.is_current.is_(True), Video.published_at.is_(None))
            )
            or 0
        )
    if from_youtube or from_files:
        log.info(
            "release dates: %d from YouTube, %d from files, %d archived still unknown",
            from_youtube,
            from_files,
            archived_missing,
        )
    return ReleaseDateResult(
        checked=len(ids),
        from_youtube=from_youtube,
        from_files=from_files,
        archived_missing=archived_missing,
        error=error,
    )
