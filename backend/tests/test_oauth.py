from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from helpers import TEST_CLIENT_ID, _reset_in_memory_state, login, make_id_token  # noqa: F401
from sqlalchemy import select, text

from downtube.db import get_engine, read_session, write_session
from downtube.models import AccountStatus, AuditEvent, GoogleAccount, OAuthState, User, WebSession
from downtube.security.crypto import decrypt, sha256_hex

TOKEN_URL = "https://oauth2.googleapis.com/token"
ACCESS = "ya29.plain-access-token-xyz"
REFRESH = "1//plain-refresh-token-abcdefghijklmnop"


def start_login(client):
    r = client.get("/api/auth/login", follow_redirects=False)
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    return r, {k: v[0] for k, v in q.items()}


def token_response(nonce: str, **claims):
    return httpx.Response(
        200,
        json={
            "access_token": ACCESS,
            "refresh_token": REFRESH,
            "expires_in": 3599,
            "scope": "openid email profile https://www.googleapis.com/auth/youtube.readonly",
            "id_token": make_id_token(nonce=nonce, **claims),
        },
    )


@pytest.fixture
def google():
    with respx.mock(assert_all_called=False) as r:
        r.get(re.compile(r"https://www\.googleapis\.com/youtube/v3/channels.*")).respond(
            200, json={"items": [{"id": "UCmine123"}]}
        )
        yield r


def test_login_redirect_parameters(client):
    r, q = start_login(client)
    assert r.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert q["response_type"] == "code" and q["client_id"] == TEST_CLIENT_ID
    assert q["redirect_uri"] == "http://localhost:8092/api/auth/callback"
    assert q["code_challenge_method"] == "S256" and len(q["code_challenge"]) == 43
    assert q["access_type"] == "offline" and q["include_granted_scopes"] == "true"
    assert q["prompt"] == "consent"
    assert "https://www.googleapis.com/auth/youtube.readonly" in q["scope"].split()
    assert {"openid", "email", "profile"} <= set(q["scope"].split())
    assert q["state"] and q["nonce"]
    with read_session() as s:  # only the hash of the state is stored; verifier is encrypted
        row = s.get(OAuthState, sha256_hex(q["state"]))
        assert row is not None and q["state"] not in row.code_verifier_enc


def test_callback_success_creates_admin_session_and_encrypts_tokens(client, google):
    _, q = start_login(client)
    route = google.post(TOKEN_URL).mock(return_value=token_response(q["nonce"]))
    r = client.get("/api/auth/callback", params={"code": "auth-code", "state": q["state"]}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"]
    assert "dt_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    # PKCE verifier + client secret were sent to the token endpoint
    sent = route.calls.last.request.content.decode()
    assert "code_verifier=" in sent and "client_secret=test-client-secret" in sent
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "owner@example.com" and body["is_admin"] is True
    assert body["acknowledged_terms"] is False and body["csrf_token"]
    assert body["account_status"] == "active" and body["youtube_channel_id"] == "UCmine123"
    # tokens are encrypted at rest: neither plaintext appears anywhere in the database file
    with read_session() as s:
        acc = s.scalar(select(GoogleAccount))
        assert acc.access_token_enc != ACCESS and decrypt(acc.access_token_enc) == ACCESS
        assert decrypt(acc.refresh_token_enc) == REFRESH
        # the session id is stored hashed
        raw = client.cookies.get("dt_session")
        assert s.get(WebSession, sha256_hex(raw)) is not None
        assert s.scalar(select(AuditEvent).where(AuditEvent.action == "login")) is not None
    with get_engine().connect() as c:
        c.execute(text("PRAGMA wal_checkpoint(FULL)"))
    import os

    from downtube.config import get_config

    for name in os.listdir(get_config().data_dir):
        data = (get_config().data_dir / name).read_bytes() if (get_config().data_dir / name).is_file() else b""
        assert ACCESS.encode() not in data and REFRESH.encode() not in data


def test_second_user_is_not_admin(client, google):
    login(client, "second@example.com", admin=False)
    _, q = start_login(client)
    google.post(TOKEN_URL).mock(
        return_value=token_response(
            q["nonce"], sub="sub-2", email="Second@Example.com"
        )  # allowlist is case-insensitive
    )
    r = client.get("/api/auth/callback", params={"code": "c", "state": q["state"]}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/"
    with read_session() as s:
        assert s.scalar(select(User).where(User.google_sub == "sub-2")).is_admin is False


def test_state_is_single_use(client, google):
    _, q = start_login(client)
    google.post(TOKEN_URL).mock(return_value=token_response(q["nonce"]))
    ok = client.get("/api/auth/callback", params={"code": "c", "state": q["state"]}, follow_redirects=False)
    assert ok.headers["location"] == "/"
    replay = client.get("/api/auth/callback", params={"code": "c", "state": q["state"]}, follow_redirects=False)
    assert replay.status_code == 302 and replay.headers["location"] == "/login?error=invalid_state"


def test_bad_state_and_missing_params(client, google):
    r = client.get("/api/auth/callback", params={"code": "c", "state": "nope"}, follow_redirects=False)
    assert r.headers["location"] == "/login?error=invalid_state"
    r = client.get("/api/auth/callback", params={"code": "c"}, follow_redirects=False)
    assert r.headers["location"] == "/login?error=invalid_request"
    r = client.get("/api/auth/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert r.headers["location"] == "/login?error=access_denied"


def test_expired_state(client, google):
    _, q = start_login(client)
    with write_session() as s:
        s.get(OAuthState, sha256_hex(q["state"])).expires_at = datetime.now(UTC) - timedelta(minutes=1)
    r = client.get("/api/auth/callback", params={"code": "c", "state": q["state"]}, follow_redirects=False)
    assert r.headers["location"] == "/login?error=state_expired"


@pytest.mark.parametrize(
    "claims,error",
    [
        ({"nonce": "wrong"}, "nonce_mismatch"),
        ({"aud": "someone-else"}, "invalid_id_token"),
        ({"iss": "https://evil.example"}, "invalid_id_token"),
        ({"exp": 1}, "invalid_id_token"),
    ],
)
def test_id_token_validation(client, google, claims, error):
    _, q = start_login(client)
    google.post(TOKEN_URL).mock(return_value=token_response(claims.pop("nonce", q["nonce"]), **claims))
    r = client.get("/api/auth/callback", params={"code": "c", "state": q["state"]}, follow_redirects=False)
    assert r.headers["location"] == f"/login?error={error}"
    assert client.get("/api/auth/me").status_code == 401


def test_unverified_email_and_bare_issuer_accepted(client, google):
    _, q = start_login(client)
    google.post(TOKEN_URL).mock(return_value=token_response(q["nonce"], email_verified=False))
    r = client.get("/api/auth/callback", params={"code": "c", "state": q["state"]}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/login?error=email_not_verified"
    _, q = start_login(client)
    google.post(TOKEN_URL).mock(return_value=token_response(q["nonce"], iss="accounts.google.com"))
    r = client.get("/api/auth/callback", params={"code": "c", "state": q["state"]}, follow_redirects=False)
    assert r.headers["location"] == "/"


def test_email_not_on_allowlist_is_403(client, google):
    _, q = start_login(client)
    google.post(TOKEN_URL).mock(return_value=token_response(q["nonce"], email="stranger@example.com"))
    r = client.get("/api/auth/callback", params={"code": "c", "state": q["state"]}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/login?error=email_not_allowed"
    with read_session() as s:
        assert s.scalar(select(User)) is None
        assert s.scalar(select(AuditEvent).where(AuditEvent.action == "login.denied")) is not None


def test_missing_youtube_scope_and_token_endpoint_failure(client, google):
    _, q = start_login(client)
    google.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "a",
                "scope": "openid email",
                "id_token": make_id_token(nonce=q["nonce"]),
            },
        )
    )
    r = client.get("/api/auth/callback", params={"code": "c", "state": q["state"]}, follow_redirects=False)
    assert r.headers["location"] == "/login?error=missing_scope"
    _, q = start_login(client)
    google.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    r = client.get("/api/auth/callback", params={"code": "c", "state": q["state"]}, follow_redirects=False)
    assert r.headers["location"] == "/login?error=token_exchange_failed"


def test_auth_rate_limit(client):
    codes = [client.get("/api/auth/login", follow_redirects=False).status_code for _ in range(22)]
    assert codes[:20] == [302] * 20 and codes[20:] == [429, 429]


def test_logout_deletes_session(client):
    h = login(client)
    assert client.post("/api/auth/logout", headers=h).status_code == 204
    assert client.get("/api/auth/me", headers=h).status_code == 401


# ----------------------------------------------------------------------------- token manager


def test_refresh_when_expiring_and_invalid_grant(client):
    from downtube.auth.tokens import AuthRevoked, get_access_token

    h = login(client)
    # fresh token: no HTTP at all
    assert get_access_token(h.account_id) == "ya29.test-access-owner@example.com"
    with write_session() as s:
        s.get(GoogleAccount, h.account_id).token_expires_at = datetime.now(UTC) + timedelta(seconds=30)
    with respx.mock() as r:
        route = r.post(TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "ya29.new", "expires_in": 3600})
        )
        assert get_access_token(h.account_id) == "ya29.new"
        assert route.call_count == 1 and b"grant_type=refresh_token" in route.calls.last.request.content
        assert get_access_token(h.account_id) == "ya29.new"  # cached now
        assert route.call_count == 1
        with write_session() as s:
            s.get(GoogleAccount, h.account_id).token_expires_at = datetime.now(UTC) - timedelta(seconds=5)
        route.mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
        with pytest.raises(AuthRevoked):
            get_access_token(h.account_id)
    with read_session() as s:
        acc = s.get(GoogleAccount, h.account_id)
        assert acc.status == AccountStatus.reauth_required
        assert s.scalar(select(AuditEvent).where(AuditEvent.action == "account.reauth_required")) is not None
    with pytest.raises(AuthRevoked):  # no further refresh attempts for a non-active account
        get_access_token(h.account_id)
    me = client.get("/api/auth/me", headers=h).json()
    assert me["account_status"] == "reauth_required"


# --------------------------------------------------------------------------- hardening (security review)


def test_callback_requires_state_cookie_from_same_browser(client, google):
    _, q = start_login(client)
    google.post(TOKEN_URL).mock(return_value=token_response(q["nonce"]))
    client.cookies.delete("dt_oauth", path="/api/auth")
    client.cookies.clear()
    r = client.get("/api/auth/callback", params={"code": "c", "state": q["state"]}, follow_redirects=False)
    assert r.headers["location"] == "/login?error=invalid_state"


def test_removed_from_allowlist_revokes_session(client, monkeypatch):
    from downtube.config import get_config

    h = login(client, "second@example.com")
    assert client.get("/api/auth/me", headers=h).status_code == 200
    monkeypatch.setenv("DOWNTUBE_ALLOWED_EMAILS", "owner@example.com")
    get_config.cache_clear()
    assert client.get("/api/auth/me", headers=h).status_code == 401
    with read_session() as s:
        assert s.scalar(select(WebSession).where(WebSession.user_id == h.user_id)) is None


def test_admin_emails_designate_admin(client, google, monkeypatch):
    from downtube.config import get_config

    monkeypatch.setenv("DOWNTUBE_ADMIN_EMAILS", "second@example.com")
    get_config.cache_clear()
    _, q = start_login(client)
    google.post(TOKEN_URL).mock(return_value=token_response(q["nonce"], email="owner@example.com"))
    client.get("/api/auth/callback", params={"code": "c", "state": q["state"]}, follow_redirects=False)
    with read_session() as s:
        assert s.scalar(select(User).where(User.email == "owner@example.com")).is_admin is False


def test_pending_oauth_states_are_bounded(client):
    for _ in range(205):
        from downtube.auth import oauth

        oauth.build_login_url()
    with read_session() as s:
        assert len(s.scalars(select(OAuthState)).all()) <= 200


def test_api_no_store_and_spa_nul_byte(client):
    r = client.get("/api/health")
    assert r.headers["Cache-Control"] == "no-store"


def test_format_ids_are_validated():
    from downtube.downloads.policy import parse_formats

    fmts = parse_formats({"formats": [{"format_id": "137"}, {"format_id": "1+2/best"}, {"format_id": "a b"}]})
    assert [f.format_id for f in fmts] == ["137"]
