"""Channel sync (uploads playlist) and large-channel estimates.

Follow phase: scan the uploads playlist from the newest page and stop at the first page that has no
unknown upload published after the rule's ``activated_at``. Backfill phase: walk every page from the
persisted ``backfill_page_token`` (resumable, one page in memory at a time). Per page, ``videos.list``
supplies duration / live / status, filters are applied and jobs are queued.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select

from downtube.auth.tokens import AuthRevoked
from downtube.db import read_session, write_session
from downtube.downloads import jobs
from downtube.models import (
    ArchiveMode,
    Availability,
    Channel,
    ChannelRule,
    DiscoverySource,
    MediaFile,
    SyncStatus,
    Video,
    VideoSource,
)
from downtube.schemas import ChannelEstimate
from downtube.settings_service import get_value
from downtube.sync import common
from downtube.sync.filters import _aware, passes_filters
from downtube.sync.metadata import resolve_pending_live
from downtube.sync.subscriptions import apply_channel_resource
from downtube.youtube.client import QuotaExceeded, YouTubeClient, YouTubeError, client_for_user
from downtube.youtube.parse import parse_time, placeholder_availability, video_from_api

log = logging.getLogger("downtube.sync.channels")

MAX_FOLLOW_PAGES = 20
MAX_BACKFILL_PAGES_PER_RUN = 100
BACKFILL_MODES = (ArchiveMode.backfill_once, ArchiveMode.backfill_and_follow)
FOLLOW_MODES = (ArchiveMode.backfill_and_follow, ArchiveMode.follow_new)

# Conservative bits/second per resolution cap, used only when no archived sample exists.
ASSUMED_BPS = {2160: 20_000_000, 1440: 10_000_000, 1080: 6_000_000, 720: 3_500_000, 480: 1_800_000, 360: 1_000_000}


@dataclass
class PageResult:
    seen: int = 0
    new_relevant: int = 0
    new_sources: int = 0
    queued: int = 0


def _entry(item: dict) -> tuple[str | None, str | None, str | None, datetime | None]:
    cd = item.get("contentDetails") or {}
    sn = item.get("snippet") or {}
    vid = cd.get("videoId") or (sn.get("resourceId") or {}).get("videoId")
    priv = (item.get("status") or {}).get("privacyStatus")
    published = parse_time(cd.get("videoPublishedAt") or sn.get("publishedAt"))
    return vid, sn.get("title"), priv, published


def _process_items(
    user_id: int,
    rule_id: int,
    items: list[dict],
    yt: YouTubeClient,
    *,
    priority: int,
    min_published: datetime | None,
    can_archive: bool,
) -> PageResult:
    res = PageResult()
    entries = []
    for item in items:
        vid, title, priv, published = _entry(item)
        if not vid:
            continue
        res.seen += 1
        if placeholder_availability(title) is not None or priv == "private":
            with write_session() as s:
                common.mark_unavailable(s, vid, placeholder_availability(title) or Availability.private)
            continue
        entries.append((vid, published))
    if not entries:
        return res
    ids = [v for v, _ in entries]
    with read_session() as s:
        have_meta = set(s.scalars(select(Video.id).where(Video.id.in_(ids), Video.duration_s.is_not(None))))
        known = set(
            s.scalars(
                select(VideoSource.video_id).where(
                    VideoSource.user_id == user_id,
                    VideoSource.channel_rule_id == rule_id,
                    VideoSource.source == DiscoverySource.subscription,
                    VideoSource.active.is_(True),
                    VideoSource.video_id.in_(ids),
                )
            )
        )
    fetched = {i["id"]: i for i in yt.videos([i for i in ids if i not in have_meta])}
    with write_session() as s:
        rule = s.get(ChannelRule, rule_id)
        if rule is None:
            return res
        cutoff = _aware(min_published)
        for vid, published in entries:
            if vid in fetched:
                video = common.upsert_video(s, video_from_api(fetched[vid]))
                data_ok = video_from_api(fetched[vid]).downloadable_now
            else:
                video = s.get(Video, vid)
                if video is None:  # not returned by videos.list: private/deleted -> unavailable
                    continue
                data_ok = video.live_status in (None, "none", "completed") and video.availability not in (
                    Availability.private,
                    Availability.deleted,
                )
            pub = _aware(video.published_at) or _aware(published)
            relevant = cutoff is None or pub is None or pub > cutoff
            if relevant and vid not in known:
                res.new_relevant += 1
            if not relevant:
                continue
            eligible = passes_filters(video, rule)
            if rule.mode == ArchiveMode.follow_new and rule.activated_at and pub is not None:
                eligible = eligible and pub > _aware(rule.activated_at)  # type: ignore[operator]
            src = common.get_source(s, user_id, vid, DiscoverySource.subscription)
            if src is None:
                src = VideoSource(user_id=user_id, video_id=vid, source=DiscoverySource.subscription)
                s.add(src)
                res.new_sources += 1
                changed = True
            else:
                # Only a state change (re-activation / newly eligible) may queue work, so a job the
                # user cancelled or that failed is never silently re-queued on every poll.
                changed = (not src.active) or (eligible and not src.archive_eligible)
            src.channel_rule_id = rule.id
            src.active = True
            src.archive_eligible = eligible
            src.last_seen_at = common.now()
            s.flush()
            if changed and eligible and can_archive and data_ok and common.enqueue_new(s, vid, user_id, priority):
                res.queued += 1
    return res


def _ensure_playlist(yt: YouTubeClient, channel_id: str) -> str:
    with read_session() as s:
        ch = s.get(Channel, channel_id)
        playlist = ch.uploads_playlist_id if ch else None
    if playlist:
        return playlist
    items = yt.channels([channel_id])
    with write_session() as s:
        ch = common.upsert_channel_stub(s, channel_id, None)
        if items:
            apply_channel_resource(ch, items[0])
        if not ch.uploads_playlist_id and channel_id.startswith("UC"):
            ch.uploads_playlist_id = "UU" + channel_id[2:]  # documented convention
        return ch.uploads_playlist_id or ""


def _set_rule(rule_id: int, **fields: object) -> None:
    with write_session() as s:
        rule = s.get(ChannelRule, rule_id)
        if rule is not None:
            for k, v in fields.items():
                setattr(rule, k, v)


def sync_channel(
    user_id: int,
    channel_id: str,
    trigger: str = "schedule",
    *,
    run_id: int | None = None,
    client: YouTubeClient | None = None,
):  # type: ignore[no-untyped-def]
    run_id = run_id or common.create_run("channel", user_id, channel_id, trigger)
    res = PageResult()
    yt = client
    rule_id: int | None = None
    try:
        with read_session() as s:
            rule = s.scalar(
                select(ChannelRule).where(ChannelRule.user_id == user_id, ChannelRule.channel_id == channel_id)
            )
            if rule is None or rule.mode == ArchiveMode.off:
                return common.finish_run(run_id, SyncStatus.ok, error="no active rule for this channel")
            if rule.paused:
                return common.finish_run(run_id, SyncStatus.ok, error="rule is paused")
            rule_id, mode = rule.id, rule.mode
            activated = rule.activated_at
            backfill_done = rule.backfill_completed_at is not None
            token = rule.backfill_page_token
            can_archive = common.user_can_archive(s, user_id)
        if mode == ArchiveMode.backfill_once and backfill_done:
            return common.finish_run(run_id, SyncStatus.ok, error="backfill already completed")
        _set_rule(rule_id, sync_status=SyncStatus.running, last_sync_at=common.now())
        if yt is None:
            yt = client_for_user(user_id, budget_fraction=0.9)  # likes keep priority on quota
        playlist = _ensure_playlist(yt, channel_id)
        if not playlist:
            raise YouTubeError(404, "no_uploads_playlist", "channel has no uploads playlist")

        def add(r: PageResult) -> None:
            res.seen += r.seen
            res.new_sources += r.new_sources
            res.queued += r.queued

        if mode in FOLLOW_MODES:
            page_token: str | None = None
            for _ in range(MAX_FOLLOW_PAGES):
                items, page_token = yt.playlist_items(playlist, page_token)
                r = _process_items(
                    user_id,
                    rule_id,
                    items,
                    yt,
                    priority=jobs.PRIORITY_SUBSCRIPTION,
                    min_published=activated,
                    can_archive=can_archive,
                )
                add(r)
                if page_token is None or r.new_relevant == 0:
                    break
        if mode in BACKFILL_MODES and not backfill_done:
            for _ in range(MAX_BACKFILL_PAGES_PER_RUN):
                items, nxt = yt.playlist_items(playlist, token)
                add(
                    _process_items(
                        user_id,
                        rule_id,
                        items,
                        yt,
                        priority=jobs.PRIORITY_BACKFILL,
                        min_published=None,
                        can_archive=can_archive,
                    )
                )
                if nxt is None:
                    _set_rule(rule_id, backfill_page_token=None, backfill_completed_at=common.now())
                    break
                _set_rule(rule_id, backfill_page_token=nxt)  # checkpoint: resume here after a deferral/crash
                token = nxt
        res.queued += resolve_pending_live(user_id, yt)
        _set_rule(rule_id, sync_status=SyncStatus.ok, last_success_at=common.now(), last_error=None)
        return common.finish_run(
            run_id,
            SyncStatus.ok,
            items_seen=res.seen,
            items_new=res.new_sources,
            jobs_queued=res.queued,
            quota_used=yt.units_used,
        )
    except QuotaExceeded as e:
        if rule_id:
            _set_rule(rule_id, sync_status=SyncStatus.quota_deferred, last_error=str(e))
        return common.finish_run(
            run_id,
            SyncStatus.quota_deferred,
            items_seen=res.seen,
            items_new=res.new_sources,
            jobs_queued=res.queued,
            quota_used=yt.units_used if yt else 0,
            error=str(e),
        )
    except Exception as e:  # noqa: BLE001 - a sync must always end with a recorded status
        revoked = isinstance(e, AuthRevoked)
        if not revoked:
            log.warning("channel sync %s failed: %s", channel_id, e)
        msg = f"reauthentication required: {e}" if revoked else f"{type(e).__name__}: {e}"
        if rule_id:
            _set_rule(rule_id, sync_status=SyncStatus.error, last_error=msg[:500])
        return common.finish_run(
            run_id,
            SyncStatus.error,
            items_seen=res.seen,
            jobs_queued=res.queued,
            quota_used=yt.units_used if yt else 0,
            error=msg,
        )
    finally:
        if client is None and yt is not None:
            yt.close()


# --------------------------------------------------------------------------- estimates


def estimate_channel(user_id: int, channel_id: str, max_height: int | None = None) -> ChannelEstimate:
    """Rough size of archiving a whole channel, with an explanation of how it was derived."""
    with read_session() as s:
        ch = s.get(Channel, channel_id)
        count = ch.video_count if ch else None
        threshold = int(get_value(s, "large_channel_threshold"))
        if max_height is None:
            rule = s.scalar(
                select(ChannelRule).where(ChannelRule.user_id == user_id, ChannelRule.channel_id == channel_id)
            )
            max_height = (rule.max_height if rule and rule.max_height else None) or get_value(s, "default_max_height")
        n_files, avg_size = s.execute(
            select(func.count(MediaFile.id), func.avg(MediaFile.size_bytes))
            .join(Video, Video.id == MediaFile.video_id)
            .where(Video.channel_id == channel_id, MediaFile.is_current.is_(True))
        ).one()
        avg_dur = s.scalar(
            select(func.avg(Video.duration_s)).where(Video.channel_id == channel_id, Video.duration_s > 0)
        )
        n_dur = s.scalar(select(func.count(Video.id)).where(Video.channel_id == channel_id, Video.duration_s > 0))
    is_large = count is not None and count > threshold
    if count is None:
        return ChannelEstimate(
            channel_id=channel_id,
            video_count=None,
            estimated_bytes=None,
            estimate_basis="The channel's video count is unknown (run a subscriptions sync); no estimate possible.",
            is_large=False,
            requires_confirmation=False,
        )
    if n_files and avg_size:
        est = int(count * float(avg_size))
        basis = f"{count} videos x average size of {n_files} already archived video(s) from this channel."
    elif avg_dur and n_dur:
        cap = max_height if max_height in ASSUMED_BPS else 2160
        est = int(count * float(avg_dur) * ASSUMED_BPS[cap] / 8)
        basis = (
            f"{count} videos x average known duration ({int(avg_dur)} s, {n_dur} sample(s)) x assumed "
            f"{ASSUMED_BPS[cap] / 1e6:.1f} Mbit/s for up to {cap}p (conservative)."
        )
    else:
        est = None
        basis = "No archived files or known durations for this channel yet; size cannot be estimated reliably."
    return ChannelEstimate(
        channel_id=channel_id,
        video_count=count,
        estimated_bytes=est,
        estimate_basis=basis,
        is_large=is_large,
        requires_confirmation=is_large,
    )
