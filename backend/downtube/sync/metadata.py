"""Metadata maintenance: finish pending live streams and refresh stale API data (retention < 30 days)."""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select

from downtube.auth.tokens import AuthRevoked
from downtube.db import read_session, write_session
from downtube.downloads import jobs
from downtube.models import AccountStatus, Channel, ChannelRule, DiscoverySource, GoogleAccount, Video, VideoSource
from downtube.sync import common
from downtube.sync.filters import passes_filters
from downtube.youtube.client import QuotaExceeded, YouTubeClient, YouTubeError
from downtube.youtube.parse import video_from_api

log = logging.getLogger("downtube.sync.metadata")


def _chunks(seq: list[str], n: int = 50):  # type: ignore[no-untyped-def]
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def resolve_pending_live(user_id: int, yt: YouTubeClient, limit: int = 50) -> int:
    """Re-check upcoming/live videos the user wants; queue those that have become downloadable."""
    with read_session() as s:
        ids = list(
            s.scalars(
                select(Video.id)
                .join(VideoSource, VideoSource.video_id == Video.id)
                .where(
                    VideoSource.user_id == user_id,
                    VideoSource.active.is_(True),
                    VideoSource.archive_eligible.is_(True),
                    Video.live_status.in_(("upcoming", "live")),
                )
                .distinct()
                .limit(limit)
            )
        )
        can = common.user_can_archive(s, user_id)
    if not ids:
        return 0
    queued = 0
    items = {i["id"]: i for i in yt.videos(ids)}
    with write_session() as s:
        for vid in ids:
            item = items.get(vid)
            if item is None:
                common.mark_unavailable(s, vid)
                continue
            data = video_from_api(item)
            video = common.upsert_video(s, data)
            if not (can and data.downloadable_now):
                continue
            best_priority = None
            for src in s.scalars(
                select(VideoSource).where(
                    VideoSource.user_id == user_id, VideoSource.video_id == vid, VideoSource.active.is_(True)
                )
            ):
                rule = s.get(ChannelRule, src.channel_rule_id) if src.channel_rule_id else None
                if rule is not None and not passes_filters(video, rule):
                    src.archive_eligible = False
                    continue
                if not src.archive_eligible:
                    continue
                p = jobs.PRIORITY_LIKE if src.source == DiscoverySource.like else jobs.PRIORITY_SUBSCRIPTION
                best_priority = p if best_priority is None else min(best_priority, p)
            if best_priority is not None and common.enqueue_new(s, vid, user_id, best_priority):
                queued += 1
    return queued


def refresh_stale_metadata(max_age_days: int = 25, *, client: YouTubeClient | None = None) -> int:
    """Refresh (or flag unavailable) videos/channels whose API data is older than ``max_age_days``.

    Local archive records are kept either way; only API-derived metadata is refreshed. Returns the
    number of rows refreshed.
    """
    cutoff = common.now() - timedelta(days=max_age_days)
    with read_session() as s:
        vids = list(
            s.scalars(
                select(Video.id).where((Video.metadata_fetched_at.is_(None)) | (Video.metadata_fetched_at < cutoff))
            )
        )
        chans = list(
            s.scalars(
                select(Channel.id).where(
                    (Channel.metadata_fetched_at.is_(None)) | (Channel.metadata_fetched_at < cutoff)
                )
            )
        )
        account_id = s.scalar(
            select(GoogleAccount.id).where(GoogleAccount.status == AccountStatus.active).order_by(GoogleAccount.id)
        )
    if account_id is None or not (vids or chans):
        return 0
    yt = client or YouTubeClient(account_id, budget_fraction=0.9)
    refreshed = 0
    try:
        for chunk in _chunks(vids):
            items = {i["id"]: i for i in yt.videos(chunk)}
            with write_session() as s:
                for vid in chunk:
                    if vid in items:
                        common.upsert_video(s, video_from_api(items[vid]))
                    else:
                        common.mark_unavailable(s, vid)
                    refreshed += 1
        for chunk in _chunks(chans):
            from downtube.sync.subscriptions import apply_channel_resource

            by_id = {i["id"]: i for i in yt.channels(chunk)}
            with write_session() as s:
                for cid in chunk:
                    ch = s.get(Channel, cid)
                    if ch is None:
                        continue
                    if cid in by_id:
                        apply_channel_resource(ch, by_id[cid])
                    else:
                        ch.metadata_fetched_at = common.now()  # gone upstream: keep local record, don't retry daily
                    refreshed += 1
    except (QuotaExceeded, AuthRevoked, YouTubeError) as e:
        log.warning("metadata refresh stopped: %s", e)
    finally:
        if client is None:
            yt.close()
    return refreshed
