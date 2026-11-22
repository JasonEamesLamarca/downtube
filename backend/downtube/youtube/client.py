"""YouTube Data API v3 client (httpx).

* Authorization comes from the token manager; a 401 triggers exactly one forced refresh.
* Every HTTP response is counted into ``quota_usage`` keyed by the America/Los_Angeles date:
  list calls cost 1 unit; write calls (videos.rate, subscriptions.delete) cost 50.
* ``QuotaExceeded`` is raised when Google answers 403 quotaExceeded/dailyLimitExceeded or when the
  locally tracked budget is reached.
* 5xx / 429 / per-minute rate limits / network errors are retried with exponential backoff + jitter.

Known API limit: ``videos.list?myRating=like`` only pages through the newest 1000 liked videos.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable

import httpx
from sqlalchemy import select

from downtube.auth.tokens import AuthRevoked, TokenRefreshError, get_access_token, mark_reauth_required
from downtube.config import get_config
from downtube.db import read_session
from downtube.models import AccountStatus, GoogleAccount
from downtube.youtube import quota

__all__ = ["AuthRevoked", "QuotaExceeded", "YouTubeClient", "YouTubeError", "client_for_user"]

log = logging.getLogger("downtube.youtube")

LIKES_MAX_ITEMS = 1000  # documented API cap for myRating=like

_THUMBS = "thumbnails(default(url),medium(url),high(url))"
VIDEO_PARTS = "snippet,contentDetails,status,liveStreamingDetails"
VIDEO_FIELDS = (
    "nextPageToken,items(id,snippet(publishedAt,channelId,channelTitle,title,description,"
    f"liveBroadcastContent,{_THUMBS}),contentDetails(duration),status(privacyStatus),"
    "liveStreamingDetails(actualStartTime,actualEndTime,scheduledStartTime))"
)
_QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded"}
_RETRY_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "backendError"}


class QuotaExceeded(Exception):  # noqa: N818 - name fixed by the contract
    pass


class YouTubeError(Exception):
    def __init__(self, status: int, reason: str, message: str = "") -> None:
        super().__init__(f"YouTube API error {status} {reason}: {message}"[:300])
        self.status = status
        self.reason = reason


def _error_reason(resp: httpx.Response) -> tuple[str, str]:
    try:
        err = resp.json().get("error", {})
        errors = err.get("errors") or [{}]
        return str(errors[0].get("reason", "")), str(err.get("message", ""))
    except (ValueError, AttributeError, IndexError):
        return "", ""


class YouTubeClient:
    def __init__(
        self,
        account_id: int,
        *,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_tries: int = 4,
        budget_fraction: float = 1.0,
    ) -> None:
        self.account_id = account_id
        self._http = http or httpx.Client(timeout=30)
        self._own_http = http is None
        self._sleep = sleep
        self.max_tries = max_tries
        self.budget_fraction = budget_fraction
        self.units_used = 0

    def __enter__(self) -> YouTubeClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._own_http:
            self._http.close()

    # ------------------------------------------------------------------ core

    def _check_budget(self, cost: int = 1) -> None:
        used, exhausted = quota.usage_today()
        if exhausted:
            raise QuotaExceeded("daily YouTube API quota exhausted")
        if used + cost > quota.budget() * self.budget_fraction:
            if self.budget_fraction >= 1.0:
                quota.mark_exhausted()
            raise QuotaExceeded("daily quota budget reached")

    def _backoff(self, attempt: int) -> None:
        self._sleep(min(30.0, 2.0**attempt) * random.uniform(0.5, 1.5))  # noqa: S311 - jitter only

    def _get(self, path: str, params: dict) -> dict:
        return self._request("GET", path, params)

    def _request(self, method: str, path: str, params: dict, cost: int = 1) -> dict:
        url = f"{get_config().youtube_api_base}/{path}"
        refreshed = False
        for attempt in range(1, self.max_tries + 1):
            self._check_budget(cost)
            try:
                token = get_access_token(self.account_id, force_refresh=False)
            except TokenRefreshError:
                if attempt >= self.max_tries:
                    raise
                self._backoff(attempt)
                continue
            try:
                resp = self._http.request(method, url, params=params, headers={"Authorization": f"Bearer {token}"})
            except httpx.TransportError:
                if attempt >= self.max_tries:
                    raise
                self._backoff(attempt)
                continue
            quota.record(cost)
            self.units_used += cost
            code = resp.status_code
            if code == 204:
                return {}
            if code == 200:
                return resp.json() if resp.content else {}
            reason, message = _error_reason(resp)
            if code == 401:
                if refreshed:
                    mark_reauth_required(self.account_id, "YouTube API rejected the access token")
                    raise AuthRevoked("access token rejected after refresh")
                refreshed = True
                try:
                    get_access_token(self.account_id, force_refresh=True)
                except TokenRefreshError:
                    pass
                continue
            if code == 403 and reason in _QUOTA_REASONS:
                quota.mark_exhausted()
                raise QuotaExceeded(reason)
            if code >= 500 or code == 429 or (code == 403 and reason in _RETRY_REASONS):
                if attempt >= self.max_tries:
                    raise YouTubeError(code, reason or "server_error", message)
                self._backoff(attempt)
                continue
            raise YouTubeError(code, reason, message)
        raise YouTubeError(0, "retries_exhausted")  # pragma: no cover

    # ------------------------------------------------------------------ resources

    @staticmethod
    def _page(data: dict) -> tuple[list[dict], str | None]:
        return data.get("items", []), data.get("nextPageToken")

    def my_channel_id(self) -> str | None:
        data = self._get("channels", {"part": "id", "mine": "true", "fields": "items(id)"})
        items = data.get("items", [])
        return items[0]["id"] if items else None

    def liked_videos(self, page_token: str | None = None) -> tuple[list[dict], str | None]:
        params = {"part": VIDEO_PARTS, "myRating": "like", "maxResults": 50, "fields": VIDEO_FIELDS}
        if page_token:
            params["pageToken"] = page_token
        return self._page(self._get("videos", params))

    def subscriptions(self, page_token: str | None = None) -> tuple[list[dict], str | None]:
        params = {
            "part": "snippet,contentDetails",
            "mine": "true",
            "maxResults": 50,
            "order": "alphabetical",
            "fields": f"nextPageToken,items(snippet(title,description,resourceId(channelId),{_THUMBS}))",
        }
        if page_token:
            params["pageToken"] = page_token
        return self._page(self._get("subscriptions", params))

    def channels(self, ids: list[str]) -> list[dict]:
        if not ids:
            return []
        if len(ids) > 50:
            raise ValueError("channels.list accepts at most 50 ids")
        params = {
            "part": "snippet,contentDetails,statistics",
            "id": ",".join(ids),
            "maxResults": 50,
            "fields": f"items(id,snippet(title,description,customUrl,{_THUMBS}),"
            "contentDetails(relatedPlaylists(uploads)),statistics(videoCount))",
        }
        return self._get("channels", params).get("items", [])

    def playlist_items(self, playlist_id: str, page_token: str | None = None) -> tuple[list[dict], str | None]:
        params = {
            "part": "snippet,contentDetails,status",
            "playlistId": playlist_id,
            "maxResults": 50,
            "fields": "nextPageToken,items(snippet(title,publishedAt,resourceId(videoId)),"
            "contentDetails(videoId,videoPublishedAt),status(privacyStatus))",
        }
        if page_token:
            params["pageToken"] = page_token
        return self._page(self._get("playlistItems", params))

    def videos(self, ids: list[str]) -> list[dict]:
        if not ids:
            return []
        if len(ids) > 50:
            raise ValueError("videos.list accepts at most 50 ids")
        params = {"part": VIDEO_PARTS, "id": ",".join(ids), "maxResults": 50, "fields": VIDEO_FIELDS}
        return self._get("videos", params).get("items", [])

    # ------------------------------------------------------------------ writes (need the manage scope)

    def subscription_id(self, channel_id: str) -> str | None:
        """The user's subscription resource id for a channel (None when not subscribed)."""
        params = {"part": "id", "mine": "true", "forChannelId": channel_id, "fields": "items(id)"}
        items = self._get("subscriptions", params).get("items", [])
        return items[0]["id"] if items else None

    def delete_subscription(self, subscription_id: str) -> None:
        self._request("DELETE", "subscriptions", {"id": subscription_id}, cost=50)

    def rate(self, video_id: str, rating: str) -> None:
        """Set the user's rating: ``like``, ``dislike`` or ``none`` (removes a like)."""
        self._request("POST", "videos/rate", {"id": video_id, "rating": rating}, cost=50)


def client_for_user(user_id: int, **kwargs) -> YouTubeClient:  # type: ignore[no-untyped-def]
    """Client for the user's active Google account; AuthRevoked when there is none."""
    with read_session() as s:
        acc = s.scalar(
            select(GoogleAccount).where(GoogleAccount.user_id == user_id, GoogleAccount.status == AccountStatus.active)
        )
        if acc is None:
            raise AuthRevoked("no active Google account")
        account_id = acc.id
    return YouTubeClient(account_id, **kwargs)
