"""Google OAuth 2.0 Authorization Code + PKCE (S256) + state + OIDC nonce, with httpx only.

The ID token is received directly from Google's token endpoint over TLS, so its claims are
validated (iss/aud/exp/nonce/email_verified) without checking the JWS signature.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from sqlalchemy import delete, select

from downtube import app_config
from downtube.auth.audit import audit
from downtube.config import get_config
from downtube.db import read_session, write_session
from downtube.models import AccountStatus, GoogleAccount, OAuthState, User
from downtube.security.crypto import decrypt, encrypt, random_token, sha256_hex

log = logging.getLogger("downtube.auth")

SCOPES = "openid email profile https://www.googleapis.com/auth/youtube.readonly"
YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
STATE_TTL = timedelta(minutes=10)
VALID_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


class OAuthError(Exception):
    """Login failed; ``code`` is a short machine-readable reason (used in /login?error=...)."""

    def __init__(self, code: str, message: str = "", status: int = 400) -> None:
        super().__init__(message or code)
        self.code = code
        self.status = status


def _pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def has_refresh_token() -> bool:
    with read_session() as s:
        return (
            s.scalar(select(GoogleAccount.id).where(GoogleAccount.refresh_token_enc.is_not(None)).limit(1)) is not None
        )


def build_login_url() -> tuple[str, str]:
    """Return (authorization URL, state). The caller binds the state to the browser via a cookie."""
    cfg = get_config()
    creds = app_config.google_creds()
    if creds is None:
        raise OAuthError("oauth_not_configured", "Google sign-in is not configured yet (open /setup)", 503)
    state, verifier, nonce = random_token(32), random_token(48), random_token(24)
    first_connect = not has_refresh_token()
    with write_session() as s:
        # Bound pending login attempts (abuse protection): drop expired, keep at most 200.
        s.execute(delete(OAuthState).where(OAuthState.expires_at < datetime.now(UTC)))
        stale = s.scalars(select(OAuthState.state_hash).order_by(OAuthState.created_at.desc()).offset(199)).all()
        if stale:
            s.execute(delete(OAuthState).where(OAuthState.state_hash.in_(stale)))
        s.add(
            OAuthState(
                state_hash=sha256_hex(state),
                code_verifier_enc=encrypt(verifier),
                nonce=nonce,
                expires_at=datetime.now(UTC) + STATE_TTL,
            )
        )
    params = {
        "client_id": creds.client_id,
        "redirect_uri": cfg.redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "nonce": nonce,
        "code_challenge": _pkce_challenge(verifier),
        "code_challenge_method": "S256",
        "access_type": "offline",
        "include_granted_scopes": "true",
    }
    if first_connect:
        params["prompt"] = "consent"  # guarantees a refresh token the first time
    return f"{cfg.google_auth_url}?{urlencode(params)}", state


def _consume_state(state: str) -> tuple[str, str]:
    """Single-use: deletes the row. Returns (code_verifier, nonce)."""
    with write_session() as s:
        row = s.get(OAuthState, sha256_hex(state))
        if row is None:
            raise OAuthError("invalid_state", "unknown or already used state")
        exp = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
        verifier_enc, nonce = row.code_verifier_enc, row.nonce
        s.delete(row)
        if exp < datetime.now(UTC):
            raise OAuthError("state_expired", "login attempt expired")
    return decrypt(verifier_enc), nonce


def decode_id_token(id_token: str) -> dict:
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError) as e:
        raise OAuthError("invalid_id_token", "malformed id_token") from e
    if not isinstance(claims, dict):
        raise OAuthError("invalid_id_token", "malformed id_token")
    return claims


def validate_claims(claims: dict, nonce: str) -> None:
    if claims.get("iss") not in VALID_ISSUERS:
        raise OAuthError("invalid_id_token", "bad issuer")
    aud = claims.get("aud")
    creds = app_config.google_creds()
    if creds is None or creds.client_id not in (aud if isinstance(aud, list) else [aud]):
        raise OAuthError("invalid_id_token", "bad audience")
    try:
        if float(claims.get("exp", 0)) < datetime.now(UTC).timestamp():
            raise OAuthError("invalid_id_token", "id_token expired")
    except (TypeError, ValueError) as e:
        raise OAuthError("invalid_id_token", "bad exp") from e
    if claims.get("nonce") != nonce:
        raise OAuthError("nonce_mismatch", "nonce mismatch")
    if claims.get("email_verified") not in (True, "true"):
        raise OAuthError("email_not_verified", "Google account email is not verified", 403)
    if not claims.get("sub") or not claims.get("email"):
        raise OAuthError("invalid_id_token", "missing sub/email")


def exchange_code(code: str, verifier: str, http: httpx.Client | None = None) -> dict:
    cfg = get_config()
    creds = app_config.google_creds()
    if creds is None:
        raise OAuthError("oauth_not_configured", "Google sign-in is not configured yet (open /setup)", 503)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": cfg.redirect_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "code_verifier": verifier,
    }
    own = http is None
    client = http or httpx.Client(timeout=20)
    try:
        resp = client.post(cfg.google_token_url, data=data)
    except httpx.HTTPError as e:
        raise OAuthError("token_exchange_failed", f"token endpoint unreachable: {type(e).__name__}") from e
    finally:
        if own:
            client.close()
    if resp.status_code != 200:
        log.warning("token exchange failed: HTTP %s", resp.status_code)
        raise OAuthError("token_exchange_failed", f"token endpoint returned {resp.status_code}")
    body = resp.json()
    if "id_token" not in body or "access_token" not in body:
        raise OAuthError("token_exchange_failed", "incomplete token response")
    return body


@dataclass
class LoginResult:
    user_id: int
    account_id: int
    email: str
    is_new_user: bool


def complete_login(code: str, state: str, ip: str | None = None, http: httpx.Client | None = None) -> LoginResult:
    verifier, nonce = _consume_state(state)
    body = exchange_code(code, verifier, http)
    claims = decode_id_token(body["id_token"])
    validate_claims(claims, nonce)
    email = claims["email"].strip().lower()
    if not app_config.is_allowed(email):
        with write_session() as s:
            audit(s, "login.denied", None, {"email": email}, ip)
        raise OAuthError("email_not_allowed", "this Google account is not allowed to use this instance", 403)
    scopes = body.get("scope", "")
    if YOUTUBE_SCOPE not in scopes.split():
        raise OAuthError("missing_scope", "the YouTube read-only permission was not granted", 400)
    now = datetime.now(UTC)
    sub = str(claims["sub"])
    with write_session() as s:
        user = s.scalar(select(User).where(User.google_sub == sub))
        is_new = user is None
        if user is None:
            first = s.scalar(select(User.id).limit(1)) is None
            admins = app_config.admin_emails(s)
            user = User(google_sub=sub, email=email, is_admin=(email in admins) if admins else first)
            s.add(user)
        elif app_config.admin_emails(s):
            user.is_admin = email in app_config.admin_emails(s)
        user.email = email
        user.display_name = claims.get("name") or user.display_name
        user.picture_url = claims.get("picture") or user.picture_url
        user.last_login_at = now
        s.flush()
        acc = s.scalar(select(GoogleAccount).where(GoogleAccount.google_sub == sub))
        if acc is None:
            acc = GoogleAccount(user_id=user.id, google_sub=sub, email=email)
            s.add(acc)
        acc.user_id = user.id
        acc.email = email
        acc.scopes = scopes
        acc.access_token_enc = encrypt(body["access_token"])
        acc.token_expires_at = now + timedelta(seconds=int(body.get("expires_in", 3600)))
        if body.get("refresh_token"):
            acc.refresh_token_enc = encrypt(body["refresh_token"])
        if acc.refresh_token_enc:
            acc.status = AccountStatus.active
            acc.status_detail = None
        else:
            acc.status = AccountStatus.reauth_required
            acc.status_detail = "Google did not return a refresh token; reconnect and grant offline access"
        s.flush()
        audit(s, "login", user.id, {"email": email, "new_user": is_new}, ip)
        result = LoginResult(user_id=user.id, account_id=acc.id, email=email, is_new_user=is_new)
    _fetch_channel_id(result.account_id, http)
    return result


def _fetch_channel_id(account_id: int, http: httpx.Client | None) -> None:
    """Best effort: remember the user's own YouTube channel id."""
    try:
        from downtube.youtube.client import YouTubeClient

        with YouTubeClient(account_id) as yt:
            cid = yt.my_channel_id()
        if cid:
            with write_session() as s:
                acc = s.get(GoogleAccount, account_id)
                if acc is not None:
                    acc.youtube_channel_id = cid
    except Exception as e:  # noqa: BLE001 - never block login on this
        log.info("could not fetch own channel id: %s", type(e).__name__)
