"""/api/auth/* endpoints."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from downtube.auth import accounts, oauth, sessions
from downtube.auth.audit import audit
from downtube.auth.deps import AuthContext, auth_context, request_db
from downtube.auth.ratelimit import client_ip, rate_limit_auth
from downtube.db import write_session
from downtube.main import ApiError
from downtube.models import GoogleAccount, User, WebSession
from downtube.schemas import DisconnectRequest, Me

log = logging.getLogger("downtube.auth")
router = APIRouter(prefix="/auth", tags=["auth"])


def _login_error(code: str) -> RedirectResponse:
    return RedirectResponse(f"/login?error={quote(code)}", status_code=302)


@router.get("/login", dependencies=[Depends(rate_limit_auth)], include_in_schema=True)
def login() -> RedirectResponse:
    try:
        return RedirectResponse(oauth.build_login_url(), status_code=302)
    except oauth.OAuthError as e:
        raise ApiError(e.status, e.code, str(e)) from e


@router.get("/callback", dependencies=[Depends(rate_limit_auth)])
def callback(
    request: Request,
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
) -> RedirectResponse:
    if error:
        return _login_error("access_denied" if error == "access_denied" else "oauth_error")
    if not code or not state:
        return _login_error("invalid_request")
    ip = client_ip(request)
    try:
        result = oauth.complete_login(code, state, ip)
    except oauth.OAuthError as e:
        log.info("login failed: %s", e.code)
        return _login_error(e.code)  # browser flow: always land on the login page with a reason
    with write_session() as s:
        raw = sessions.create_session(s, result.user_id, request.headers.get("user-agent"))
    resp = RedirectResponse("/", status_code=302)
    sessions.set_cookie(resp, raw)
    return resp


def _me(db: Session, u: User, csrf_token: str) -> Me:
    acc = db.scalar(select(GoogleAccount).where(GoogleAccount.user_id == u.id))
    return Me(
        id=u.id,
        email=u.email,
        display_name=u.display_name,
        picture_url=u.picture_url,
        is_admin=u.is_admin,
        acknowledged_terms=u.acknowledged_terms_at is not None,
        account_status=acc.status.value if acc else "disconnected",  # type: ignore[arg-type]
        youtube_channel_id=acc.youtube_channel_id if acc else None,
        csrf_token=csrf_token,
    )


@router.get("/me", response_model=Me)
def me(ctx: AuthContext = Depends(auth_context), db: Session = Depends(request_db)) -> Me:
    return _me(db, ctx.user, ctx.session.csrf_token)


@router.post("/logout", status_code=204)
def logout(request: Request, ctx: AuthContext = Depends(auth_context)) -> Response:
    with write_session() as w:
        w.execute(delete(WebSession).where(WebSession.id_hash == ctx.session.id_hash))
        audit(w, "logout", ctx.user.id, None, client_ip(request))
    resp = Response(status_code=204)
    sessions.clear_cookie(resp)
    return resp


@router.post("/acknowledge", response_model=Me)
def acknowledge(request: Request, ctx: AuthContext = Depends(auth_context)) -> Me:
    with write_session() as w:
        user = w.get(User, ctx.user.id)
        assert user is not None
        if user.acknowledged_terms_at is None:
            user.acknowledged_terms_at = datetime.now(UTC)
            audit(w, "terms.acknowledged", user.id, None, client_ip(request))
        return _me(w, user, ctx.session.csrf_token)


@router.post("/disconnect", status_code=204)
def disconnect(
    body: DisconnectRequest,
    request: Request,
    ctx: AuthContext = Depends(auth_context),
) -> Response:
    accounts.disconnect(
        ctx.user.id,
        revoke=body.revoke_google_token,
        delete_local_data=body.delete_local_data,
        ip=client_ip(request),
    )
    resp = Response(status_code=204)
    if body.delete_local_data:
        sessions.clear_cookie(resp)
    return resp
