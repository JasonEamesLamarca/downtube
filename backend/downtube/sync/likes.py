"""Liked-videos sync (CONTRACTS section 7).

* incremental: page ``myRating=like`` (newest like first) until a page has no unseen id (max 3 pages)
* full: walk every page; ids no longer liked become ``active=False`` (archives are never touched)
* initial scan: full walk; with ``likes_mode=future_only`` everything found is a non-archivable baseline

The API only exposes the newest 1000 likes, so a full walk that reaches that cap never deactivates
anything (older likes are simply invisible, not removed).
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from downtube.auth.tokens import AuthRevoked
from downtube.db import read_session, write_session
from downtube.downloads import jobs
from downtube.models import DiscoverySource, SyncRun, SyncStatus, Video, VideoSource
from downtube.settings_service import get_value
from downtube.sync import common
from downtube.sync.metadata import resolve_pending_live
from downtube.youtube.client import LIKES_MAX_ITEMS, QuotaExceeded, YouTubeClient, YouTubeError, client_for_user
from downtube.youtube.parse import video_from_api

log = logging.getLogger("downtube.sync.likes")

MAX_INCREMENTAL_PAGES = 3
CHECKPOINT = "likes"


class _Counters:
    def __init__(self) -> None:
        self.seen = 0
        self.new = 0
        self.queued = 0


def _process_page(
    user_id: int, items: list[dict], *, eligible: bool, enqueue_ok: bool, seen_ids: set[str], c: _Counters
) -> int:
    """Persist one page; returns the number of unseen likes on it."""
    unseen = 0
    with write_session() as s:
        for item in items:
            data = video_from_api(item)
            prev = s.get(Video, data.id)
            was_pending = prev is not None and prev.live_status in ("upcoming", "live")
            common.upsert_video(s, data)
            seen_ids.add(data.id)
            c.seen += 1
            src = common.get_source(s, user_id, data.id, DiscoverySource.like)
            is_new = False
            if src is None:
                src = VideoSource(
                    user_id=user_id,
                    video_id=data.id,
                    source=DiscoverySource.like,
                    active=True,
                    archive_eligible=eligible,
                )
                s.add(src)
                is_new = True
            else:
                src.last_seen_at = common.now()
                if not src.active:  # re-liked after an unlike
                    src.active = True
                    src.archive_eligible = eligible
                    is_new = True
            if is_new:
                unseen += 1
                c.new += 1
            s.flush()
            # a stream that was upcoming/live at discovery becomes archivable once it has finished
            if (is_new or was_pending) and src.archive_eligible and enqueue_ok and data.downloadable_now:
                if common.enqueue_new(s, data.id, user_id, jobs.PRIORITY_LIKE):
                    c.queued += 1
    return unseen


def sync_likes(
    user_id: int,
    *,
    full: bool = False,
    trigger: str = "schedule",
    run_id: int | None = None,
    client: YouTubeClient | None = None,
) -> SyncRun:
    run_id = run_id or common.create_run("likes_full" if full else "likes", user_id, None, trigger)
    c = _Counters()
    own_client = client is None
    yt: YouTubeClient | None = client
    try:
        with read_session() as s:
            mode = get_value(s, "likes_mode")
            checkpoint = common.get_checkpoint(s, user_id, CHECKPOINT)
            enqueue_ok = common.user_can_archive(s, user_id)
        if mode == "off":
            return common.finish_run(run_id, SyncStatus.ok, error="likes_mode is off; nothing synced")
        initial = not (checkpoint and checkpoint.get("baselined"))
        walk_all = full or initial
        # Baseline of a future_only scan (and anything discovered before the user acknowledged the
        # notice) is recorded but not archivable until the user asks for a backfill.
        eligible = enqueue_ok and (mode == "all" or not initial)
        if yt is None:
            yt = client_for_user(user_id)
        seen_ids: set[str] = set()
        token: str | None = None
        pages = 0
        complete = False
        while True:
            items, token = yt.liked_videos(token)
            pages += 1
            unseen = _process_page(user_id, items, eligible=eligible, enqueue_ok=enqueue_ok, seen_ids=seen_ids, c=c)
            if token is None or (c.seen >= LIKES_MAX_ITEMS and walk_all):
                complete = True
                break
            if not walk_all and (unseen == 0 or pages >= MAX_INCREMENTAL_PAGES):
                break
        capped = c.seen >= LIKES_MAX_ITEMS
        with write_session() as s:
            if complete and walk_all:
                if not capped:
                    for src in s.scalars(
                        select(VideoSource).where(
                            VideoSource.user_id == user_id,
                            VideoSource.source == DiscoverySource.like,
                            VideoSource.active.is_(True),
                        )
                    ):
                        if src.video_id not in seen_ids:
                            src.active = False
                if initial:
                    common.set_checkpoint(s, user_id, CHECKPOINT, {"baselined": True, "at": common.now().isoformat()})
        c.queued += resolve_pending_live(user_id, yt)
        return common.finish_run(
            run_id, SyncStatus.ok, items_seen=c.seen, items_new=c.new, jobs_queued=c.queued, quota_used=yt.units_used
        )
    except QuotaExceeded as e:
        return common.finish_run(
            run_id,
            SyncStatus.quota_deferred,
            items_seen=c.seen,
            items_new=c.new,
            jobs_queued=c.queued,
            quota_used=yt.units_used if yt else 0,
            error=str(e),
        )
    except AuthRevoked as e:
        return common.finish_run(run_id, SyncStatus.error, items_seen=c.seen, error=f"reauthentication required: {e}")
    except (YouTubeError, OSError, RuntimeError) as e:
        log.warning("likes sync failed: %s", e)
        return common.finish_run(
            run_id,
            SyncStatus.error,
            items_seen=c.seen,
            items_new=c.new,
            quota_used=yt.units_used if yt else 0,
            error=str(e),
        )
    except Exception as e:  # noqa: BLE001 - a sync must always end with a recorded status
        log.exception("likes sync crashed")
        return common.finish_run(run_id, SyncStatus.error, items_seen=c.seen, error=f"{type(e).__name__}: {e}")
    finally:
        if own_client and yt is not None:
            yt.close()
