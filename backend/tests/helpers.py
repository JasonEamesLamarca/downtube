"""Reusable test helpers: fake sessions (``login``) and a respx-backed fake YouTube/Google API.

Usage in a test module::

    from helpers import fake_yt, login            # noqa: F401  (fixtures are imported)

    def test_x(client):
        h = login(client, "owner@example.com")    # headers incl. cookie + X-CSRF-Token
        assert client.get("/api/auth/me", headers=h).status_code == 200
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from sqlalchemy import select

TEST_CLIENT_ID = "test-client-id.apps.googleusercontent.com"


class Auth(dict):
    """Request headers plus identifiers: ``h = login(client)``; pass ``headers=h``."""

    user_id: int
    account_id: int | None
    email: str
    csrf: str


def login(
    client,  # noqa: ANN001 - TestClient (ensures the app/DB is initialised)
    email: str = "owner@example.com",
    *,
    admin: bool | None = None,
    acknowledged: bool = True,
    with_account: bool = True,
    status: str = "active",
) -> Auth:
    """Create (or reuse) a user + Google account + web session directly in the DB."""
    from downtube.auth.sessions import create_session
    from downtube.db import write_session
    from downtube.models import AccountStatus, GoogleAccount, User, WebSession
    from downtube.security.crypto import encrypt, sha256_hex

    with write_session() as s:
        user = s.scalar(select(User).where(User.email == email))
        if user is None:
            first = s.scalar(select(User.id).limit(1)) is None
            user = User(
                google_sub=f"sub-{email}",
                email=email,
                display_name=email.split("@")[0],
                is_admin=first if admin is None else admin,
            )
            s.add(user)
            s.flush()
        if acknowledged and user.acknowledged_terms_at is None:
            user.acknowledged_terms_at = datetime.now(UTC)
        account_id = None
        if with_account:
            acc = s.scalar(select(GoogleAccount).where(GoogleAccount.user_id == user.id))
            if acc is None:
                acc = GoogleAccount(user_id=user.id, google_sub=user.google_sub, email=email)
                s.add(acc)
            acc.access_token_enc = encrypt("ya29.test-access-" + email)
            acc.refresh_token_enc = encrypt("1//test-refresh-token-0123456789-" + email)
            acc.token_expires_at = datetime.now(UTC) + timedelta(hours=1)
            acc.status = AccountStatus(status)
            s.flush()
            account_id = acc.id
        raw = create_session(s, user.id)
        sess = s.get(WebSession, sha256_hex(raw))
        auth = Auth({"Cookie": f"dt_session={raw}", "X-CSRF-Token": sess.csrf_token})
        auth.user_id, auth.account_id, auth.email, auth.csrf = user.id, account_id, email, sess.csrf_token
        return auth


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_id_token(**overrides) -> str:  # type: ignore[no-untyped-def]
    claims = {
        "iss": "https://accounts.google.com",
        "aud": TEST_CLIENT_ID,
        "sub": "google-sub-1",
        "email": "owner@example.com",
        "email_verified": True,
        "name": "Owner",
        "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
        "nonce": "n",
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return ".".join([b64url(b'{"alg":"RS256"}'), b64url(json.dumps(claims).encode()), "sig"])


def video_item(
    vid: str,
    *,
    title: str | None = None,
    duration: str = "PT10M",
    channel: str = "UCchan1",
    published: str = "2026-01-01T00:00:00Z",
    privacy: str = "public",
    live: dict | None = None,
    broadcast: str = "none",
) -> dict:
    item = {
        "id": vid,
        "snippet": {
            "publishedAt": published,
            "channelId": channel,
            "channelTitle": f"Channel {channel}",
            "title": title or f"Video {vid}",
            "description": "d",
            "liveBroadcastContent": broadcast,
            "thumbnails": {"medium": {"url": f"https://i.ytimg.com/vi/{vid}/mq.jpg"}},
        },
        "contentDetails": {"duration": duration},
        "status": {"privacyStatus": privacy},
    }
    if live is not None:
        item["liveStreamingDetails"] = live
    return item


def vid_id(n: int) -> str:
    return f"vid{n:08d}"[:11]


class FakeYouTube:
    """In-memory stand-in for the YouTube Data API behind a respx router."""

    def __init__(self, router: respx.MockRouter) -> None:
        self.videos: dict[str, dict] = {}
        self.likes_pages: list[list[str]] = []
        self.subscriptions: list[list[dict]] = []  # pages of subscription items
        self.channel_items: dict[str, dict] = {}
        self.playlists: dict[str, list[list[str]]] = {}  # playlist id -> pages of video ids
        self.placeholders: dict[str, str] = {}  # video id -> placeholder title in playlist
        self.my_channel = "UCmine"
        self.fail: list[httpx.Response] = []  # consumed first, one per request
        self.fail_when = None  # optional callable(path, params) -> Response | None
        self.calls: list[tuple[str, dict]] = []
        router.route(host="www.googleapis.com").mock(side_effect=self._handle)

    # -- builders
    def add_video(self, vid: str, **kw) -> str:  # type: ignore[no-untyped-def]
        self.videos[vid] = video_item(vid, **kw)
        return vid

    def set_likes(self, *pages: list[str]) -> None:
        for page in pages:
            for v in page:
                self.videos.setdefault(v, video_item(v))
        self.likes_pages = [list(p) for p in pages] or [[]]

    def add_channel(self, cid: str, *, title: str | None = None, video_count: int = 10) -> None:
        self.channel_items[cid] = {
            "id": cid,
            "snippet": {"title": title or f"Channel {cid}", "description": "", "customUrl": f"@{cid}"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU" + cid[2:]}},
            "statistics": {"videoCount": str(video_count)},
        }

    def add_subscription_page(self, *cids: str) -> None:
        page = []
        for c in cids:
            self.add_channel(c) if c not in self.channel_items else None
            page.append({"snippet": {"title": f"Channel {c}", "description": "", "resourceId": {"channelId": c}}})
        self.subscriptions.append(page)

    def set_uploads(self, cid: str, *pages: list[str]) -> None:
        for page in pages:
            for v in page:
                self.videos.setdefault(v, video_item(v, channel=cid))
        self.playlists["UU" + cid[2:]] = [list(p) for p in pages]

    def count(self, path: str) -> int:
        return sum(1 for p, _ in self.calls if p == path)

    # -- dispatch
    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.rsplit("/", 1)[-1]
        q = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        self.calls.append((path, q))
        if self.fail:
            return self.fail.pop(0)
        if self.fail_when is not None and (resp := self.fail_when(path, q)) is not None:
            return resp
        tok = q.get("pageToken")

        def paged(pages: list, render):  # type: ignore[no-untyped-def]
            idx = int(tok[1:]) if tok else 0
            body: dict = {"items": [render(x) for x in pages[idx]] if idx < len(pages) else []}
            if idx + 1 < len(pages):
                body["nextPageToken"] = f"p{idx + 1}"
            return httpx.Response(200, json=body)

        if path == "videos":
            if q.get("myRating") == "like":
                return paged(self.likes_pages or [[]], lambda v: self.videos[v])
            ids = q.get("id", "").split(",")
            return httpx.Response(200, json={"items": [self.videos[i] for i in ids if i in self.videos]})
        if path == "subscriptions":
            return paged(self.subscriptions or [[]], lambda x: x)
        if path == "channels":
            if q.get("mine") == "true":
                return httpx.Response(200, json={"items": [{"id": self.my_channel}]})
            ids = q.get("id", "").split(",")
            return httpx.Response(200, json={"items": [self.channel_items[i] for i in ids if i in self.channel_items]})
        if path == "playlistItems":
            pages = self.playlists.get(q["playlistId"])
            if pages is None:
                return httpx.Response(404, json={"error": {"errors": [{"reason": "playlistNotFound"}], "message": "x"}})
            return paged(pages, self._playlist_item)
        return httpx.Response(404, json={})

    def _playlist_item(self, vid: str) -> dict:
        v = self.videos.get(vid, video_item(vid))
        title = self.placeholders.get(vid, v["snippet"]["title"])
        return {
            "snippet": {"title": title, "publishedAt": v["snippet"]["publishedAt"], "resourceId": {"videoId": vid}},
            "contentDetails": {"videoId": vid, "videoPublishedAt": v["snippet"]["publishedAt"]},
            "status": {"privacyStatus": "public"},
        }


def quota_error(reason: str = "quotaExceeded") -> httpx.Response:
    return httpx.Response(403, json={"error": {"errors": [{"reason": reason}], "message": "quota"}})


@pytest.fixture
def fake_yt(client):  # type: ignore[no-untyped-def]
    with respx.mock(assert_all_called=False) as router:
        yield FakeYouTube(router)


@pytest.fixture(autouse=True)
def _reset_in_memory_state():  # type: ignore[no-untyped-def]
    from downtube.auth.ratelimit import auth_limiter
    from downtube.sync import runner

    auth_limiter.reset()
    runner.reset_guard()
    yield
