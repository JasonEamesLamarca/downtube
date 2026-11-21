from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from helpers import _reset_in_memory_state, fake_yt, login, quota_error  # noqa: F401
from sqlalchemy import select

from downtube.auth.tokens import AuthRevoked
from downtube.db import read_session, write_session
from downtube.models import AccountStatus, GoogleAccount
from downtube.youtube import quota
from downtube.youtube.client import QuotaExceeded, YouTubeClient, YouTubeError

VIDEOS = "https://www.googleapis.com/youtube/v3/videos"
TOKEN = "https://oauth2.googleapis.com/token"


@pytest.fixture
def yt(client):
    h = login(client)
    sleeps: list[float] = []
    c = YouTubeClient(h.account_id, sleep=sleeps.append)
    c.sleeps = sleeps  # type: ignore[attr-defined]
    c.auth = h  # type: ignore[attr-defined]
    yield c
    c.close()


def test_success_sends_bearer_and_counts_quota_in_pacific_day(yt):
    with respx.mock() as r:
        route = r.get(VIDEOS).respond(200, json={"items": [{"id": "a"}], "nextPageToken": "t"})
        items, nxt = yt.liked_videos()
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer ya29.test-access-owner@example.com"
    assert req.url.params["myRating"] == "like" and req.url.params["maxResults"] == "50"
    assert items == [{"id": "a"}] and nxt == "t"
    assert yt.units_used == 1 and quota.usage_today() == (1, False)
    assert quota.pt_day(datetime(2026, 11, 21, 3, 0, tzinfo=UTC)) == "2026-11-20"  # PT is behind UTC


def test_retries_5xx_with_backoff_then_succeeds(yt):
    with respx.mock() as r:
        r.get(VIDEOS).mock(
            side_effect=[httpx.Response(503), httpx.Response(429), httpx.Response(200, json={"items": []})]
        )
        assert yt.videos(["abc"]) == []
    assert len(yt.sleeps) == 2 and yt.sleeps[1] > yt.sleeps[0] * 0.9  # growing (jittered) delays
    assert yt.units_used == 3


def test_gives_up_after_max_tries(yt):
    with respx.mock() as r:
        route = r.get(VIDEOS).respond(500)
        with pytest.raises(YouTubeError) as e:
            yt.videos(["abc"])
    assert route.call_count == 4 and e.value.status == 500


def test_retries_network_errors_and_rate_limit_reason(yt):
    with respx.mock() as r:
        r.get(VIDEOS).mock(
            side_effect=[
                httpx.ConnectError("boom"),
                quota_error("rateLimitExceeded"),
                httpx.Response(200, json={"items": []}),
            ]
        )
        assert yt.videos(["abc"]) == []
    assert len(yt.sleeps) == 2


@pytest.mark.parametrize("reason", ["quotaExceeded", "dailyLimitExceeded"])
def test_quota_exceeded_marks_exhausted_and_short_circuits(yt, reason):
    with respx.mock() as r:
        route = r.get(VIDEOS).mock(return_value=quota_error(reason))
        with pytest.raises(QuotaExceeded):
            yt.videos(["abc"])
        assert quota.usage_today()[1] is True
        with pytest.raises(QuotaExceeded):  # no further HTTP once exhausted
            yt.videos(["abc"])
        assert route.call_count == 1


def test_other_403_is_a_plain_error(yt):
    with respx.mock() as r:
        r.get(VIDEOS).mock(
            return_value=httpx.Response(403, json={"error": {"errors": [{"reason": "forbidden"}], "message": "no"}})
        )
        with pytest.raises(YouTubeError) as e:
            yt.videos(["abc"])
    assert e.value.reason == "forbidden"


def test_401_refreshes_once_then_retries(yt):
    with respx.mock() as r:
        r.get(VIDEOS).mock(side_effect=[httpx.Response(401), httpx.Response(200, json={"items": []})])
        tok = r.post(TOKEN).respond(200, json={"access_token": "ya29.fresh", "expires_in": 3600})
        assert yt.videos(["abc"]) == []
        assert tok.call_count == 1
        assert r.calls[-1].request.headers["authorization"] == "Bearer ya29.fresh"


def test_persistent_401_means_auth_revoked(yt):
    with respx.mock() as r:
        r.get(VIDEOS).respond(401)
        r.post(TOKEN).respond(200, json={"access_token": "ya29.fresh", "expires_in": 3600})
        with pytest.raises(AuthRevoked):
            yt.videos(["abc"])
    with read_session() as s:
        assert s.scalar(select(GoogleAccount)).status == AccountStatus.reauth_required


def test_expiring_token_is_refreshed_before_the_call(yt):
    with write_session() as s:
        s.get(GoogleAccount, yt.account_id).token_expires_at = datetime.now(UTC) + timedelta(seconds=10)
    with respx.mock() as r:
        r.post(TOKEN).respond(200, json={"access_token": "ya29.rotated", "expires_in": 3600})
        r.get(VIDEOS).respond(200, json={"items": []})
        yt.videos(["abc"])
        assert r.calls[-1].request.headers["authorization"] == "Bearer ya29.rotated"


def test_batch_limits_and_parts(yt):
    with pytest.raises(ValueError):
        yt.videos([str(i) for i in range(51)])
    with pytest.raises(ValueError):
        yt.channels([str(i) for i in range(51)])
    with respx.mock() as r:
        route = r.get(VIDEOS).respond(200, json={"items": []})
        yt.videos(["a", "b"])
        p = route.calls.last.request.url.params
        assert p["id"] == "a,b" and p["part"] == "snippet,contentDetails,status,liveStreamingDetails"
