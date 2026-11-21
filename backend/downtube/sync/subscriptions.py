"""Subscriptions sync: mirror subscriptions.list and enrich channels via channels.list (batches of 50)."""

from __future__ import annotations

import logging

from sqlalchemy import select

from downtube.auth.tokens import AuthRevoked
from downtube.db import write_session
from downtube.models import Channel, Subscription, SyncStatus
from downtube.sync import common
from downtube.youtube.client import QuotaExceeded, YouTubeClient, client_for_user
from downtube.youtube.parse import best_thumbnail

log = logging.getLogger("downtube.sync.subscriptions")


def apply_channel_resource(ch: Channel, item: dict) -> None:
    snippet = item.get("snippet") or {}
    ch.title = snippet.get("title") or ch.title
    ch.description = snippet.get("description", ch.description)
    ch.custom_url = snippet.get("customUrl") or ch.custom_url
    ch.thumbnail_url = best_thumbnail(snippet) or ch.thumbnail_url
    uploads = ((item.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
    if uploads:
        ch.uploads_playlist_id = uploads
    count = (item.get("statistics") or {}).get("videoCount")
    if count is not None:
        try:
            ch.video_count = int(count)
        except (TypeError, ValueError):
            pass
    ch.metadata_fetched_at = common.now()


def sync_subscriptions(
    user_id: int, trigger: str = "schedule", *, run_id: int | None = None, client: YouTubeClient | None = None
):  # type: ignore[no-untyped-def]
    run_id = run_id or common.create_run("subscriptions", user_id, None, trigger)
    seen = new = 0
    yt = client
    try:
        if yt is None:
            yt = client_for_user(user_id)
        channel_ids: list[str] = []
        token: str | None = None
        while True:
            items, token = yt.subscriptions(token)
            with write_session() as s:
                for item in items:
                    snippet = item.get("snippet") or {}
                    cid = (snippet.get("resourceId") or {}).get("channelId")
                    if not cid:
                        continue
                    ch = common.upsert_channel_stub(s, cid, snippet.get("title"))
                    if not ch.thumbnail_url:
                        ch.thumbnail_url = best_thumbnail(snippet)
                    if ch.description is None:
                        ch.description = snippet.get("description")
                    sub = s.scalar(
                        select(Subscription).where(Subscription.user_id == user_id, Subscription.channel_id == cid)
                    )
                    if sub is None:
                        s.add(Subscription(user_id=user_id, channel_id=cid))
                        new += 1
                    else:
                        sub.active = True
                        sub.last_seen_at = common.now()
                    channel_ids.append(cid)
                    seen += 1
            if token is None:
                break
        with write_session() as s:  # unsubscribed upstream -> inactive (rules/archives untouched)
            keep = set(channel_ids)
            for sub in s.scalars(
                select(Subscription).where(Subscription.user_id == user_id, Subscription.active.is_(True))
            ):
                if sub.channel_id not in keep:
                    sub.active = False
        unique = list(dict.fromkeys(channel_ids))
        for i in range(0, len(unique), 50):
            chunk = unique[i : i + 50]
            by_id = {c["id"]: c for c in yt.channels(chunk)}
            with write_session() as s:
                for cid in chunk:
                    ch = s.get(Channel, cid)
                    if ch is not None and cid in by_id:
                        apply_channel_resource(ch, by_id[cid])
        return common.finish_run(run_id, SyncStatus.ok, items_seen=seen, items_new=new, quota_used=yt.units_used)
    except QuotaExceeded as e:
        return common.finish_run(
            run_id,
            SyncStatus.quota_deferred,
            items_seen=seen,
            items_new=new,
            quota_used=yt.units_used if yt else 0,
            error=str(e),
        )
    except AuthRevoked as e:
        return common.finish_run(run_id, SyncStatus.error, error=f"reauthentication required: {e}")
    except Exception as e:  # noqa: BLE001 - a sync must always end with a recorded status
        log.warning("subscriptions sync failed: %s", e)
        return common.finish_run(run_id, SyncStatus.error, items_seen=seen, error=f"{type(e).__name__}: {e}")
    finally:
        if client is None and yt is not None:
            yt.close()
