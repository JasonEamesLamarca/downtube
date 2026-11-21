"""Pure helpers turning YouTube API resources into plain data."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from downtube.models import Availability

SHORT_MAX_S = 180

_DUR = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")
PLACEHOLDER_TITLES = {"private video": Availability.private, "deleted video": Availability.deleted}


def parse_duration(value: str | None) -> int | None:
    """ISO-8601 duration (``PT1H2M3S``, ``P1DT2H``) -> whole seconds; None if unparseable."""
    if not value:
        return None
    m = _DUR.match(value.strip())
    if not m or not any(m.groups()):
        return 0 if value.strip() in ("P0D", "PT0S") else None
    d, h, mi, sec = m.groups()
    return int((int(d or 0) * 86400) + (int(h or 0) * 3600) + (int(mi or 0) * 60) + float(sec or 0))


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def best_thumbnail(snippet: dict) -> str | None:
    thumbs = snippet.get("thumbnails") or {}
    for key in ("medium", "high", "default", "standard"):
        url = (thumbs.get(key) or {}).get("url")
        if url:
            return url
    return None


def placeholder_availability(title: str | None) -> Availability | None:
    return PLACEHOLDER_TITLES.get((title or "").strip().lower())


@dataclass
class VideoData:
    id: str
    channel_id: str | None
    channel_title: str | None
    title: str | None
    description: str | None
    published_at: datetime | None
    duration_s: int | None
    is_short: bool | None
    is_live: bool
    live_status: str  # none | upcoming | live | completed
    availability: Availability
    thumbnail_url: str | None

    @property
    def downloadable_now(self) -> bool:
        return self.live_status in ("none", "completed") and self.availability in (
            Availability.public,
            Availability.unlisted,
            Availability.unknown,
        )


def video_from_api(item: dict) -> VideoData:
    """Normalise a ``videos`` resource (parts snippet, contentDetails, status, liveStreamingDetails)."""
    snippet = item.get("snippet") or {}
    status = item.get("status") or {}
    live = item.get("liveStreamingDetails")
    duration = parse_duration((item.get("contentDetails") or {}).get("duration"))
    title = snippet.get("title")
    availability = placeholder_availability(title) or {
        "public": Availability.public,
        "unlisted": Availability.unlisted,
        "private": Availability.private,
    }.get(status.get("privacyStatus", ""), Availability.unknown)
    broadcast = snippet.get("liveBroadcastContent", "none")
    live_status = "none"
    if live:
        if broadcast in ("upcoming", "live"):
            live_status = broadcast
        elif live.get("actualEndTime"):
            live_status = "completed"
        elif live.get("actualStartTime"):
            live_status = "live"
        elif live.get("scheduledStartTime"):
            live_status = "upcoming"
        else:
            live_status = "completed"
    elif broadcast in ("upcoming", "live"):
        live_status = broadcast
    return VideoData(
        id=item["id"],
        channel_id=snippet.get("channelId"),
        channel_title=snippet.get("channelTitle"),
        title=title,
        description=snippet.get("description"),
        published_at=parse_time(snippet.get("publishedAt")),
        duration_s=duration,
        is_short=(duration is not None and 0 < duration <= SHORT_MAX_S and not live),
        is_live=bool(live),
        live_status=live_status,
        availability=availability,
        thumbnail_url=best_thumbnail(snippet),
    )
