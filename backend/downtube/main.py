"""FastAPI application factory and process lifecycle."""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from downtube import __version__
from downtube.config import Config, get_config
from downtube.db import init_engine
from downtube.logging_setup import setup_logging
from downtube.migrate import upgrade_db

log = logging.getLogger("downtube")


class ApiError(HTTPException):
    """Raise with a stable machine-readable ``code``; rendered as {"error": {...}}."""

    def __init__(self, status_code: int, code: str, message: str, details: dict | None = None) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.details = details


def _error(status: int, code: str, message: str, details: dict | None = None) -> JSONResponse:
    body: dict = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return JSONResponse(status_code=status, content=body)


_STATUS_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    429: "rate_limited",
    503: "unavailable",
}


def create_app(config: Config | None = None, *, start_background: bool | None = None) -> FastAPI:
    cfg = config or get_config()
    background = (not cfg.disable_background) if start_background is None else start_background

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        setup_logging(cfg)
        for problem in cfg.validate_runtime():
            log.warning("config: %s", problem)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.temp_dir.mkdir(parents=True, exist_ok=True)
        init_engine(db_path=cfg.db_path)
        upgrade_db()
        _announce_setup_code(cfg)
        if background:
            from downtube import scheduler
            from downtube.downloads import importer, manager

            manager.startup()
            importer.clean_stale_uploads()
            scheduler.start()
        log.info("DownTube %s started (base_url=%s)", __version__, cfg.base_url)
        try:
            yield
        finally:
            if background:
                from downtube import scheduler
                from downtube.downloads import manager

                scheduler.stop()
                manager.shutdown()
            log.info("DownTube stopped")

    app = FastAPI(
        title="DownTube",
        version=__version__,
        description="Self-hosted YouTube archiving API. Not affiliated with or endorsed by Google or YouTube.",
        lifespan=lifespan,
        docs_url=None,  # served below, only to signed-in users
        redoc_url=None,
        openapi_url=None,
    )

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error(exc.status_code, exc.code, str(exc.detail), exc.details)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error(exc.status_code, _STATUS_CODES.get(exc.status_code, "error"), str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errs = [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()]
        return _error(422, "validation_error", "request validation failed", {"errors": errs})

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/library/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        if hsts:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        if request.url.path == "/api/docs":  # Swagger UI assets come from jsDelivr
            csp = (
                "default-src 'self'; img-src 'self' data: https://fastapi.tiangolo.com; "
                "style-src 'self' https://cdn.jsdelivr.net; script-src 'self' 'unsafe-inline' "
                "https://cdn.jsdelivr.net; connect-src 'self'; frame-ancestors 'none'"
            )
        else:
            csp = (
                "default-src 'self'; img-src 'self' https://i.ytimg.com https://yt3.ggpht.com "
                "https://*.googleusercontent.com data:; media-src 'self'; style-src 'self' 'unsafe-inline'; "
                "script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
            )
        resp.headers.setdefault("Content-Security-Policy", csp)
        return resp

    hsts = cfg.base_url.startswith("https://")
    app.add_middleware(TrustedProxyMiddleware, config=cfg)

    from downtube.api import api_router
    from downtube.auth.deps import current_user

    app.include_router(api_router, prefix="/api")

    @app.get("/api/openapi.json", include_in_schema=False, dependencies=[Depends(current_user)])
    async def openapi_json() -> JSONResponse:
        return JSONResponse(app.openapi())

    @app.get("/api/docs", include_in_schema=False, dependencies=[Depends(current_user)])
    async def swagger_ui() -> HTMLResponse:
        return get_swagger_ui_html(openapi_url="/api/openapi.json", title="DownTube API")

    _mount_spa(app, cfg.static_dir)
    return app


def _announce_setup_code(cfg: Config) -> None:
    """In setup mode, issue a fresh one-time setup code and print it to the log."""
    from downtube import app_config
    from downtube.db import write_session

    with write_session() as s:
        if not app_config.needs_setup(s):
            return
        code = app_config.new_setup_code(s)
    bar = "=" * 64
    log.warning(
        "\n%s\nDownTube is not set up yet. Open %s/setup and enter\n  DownTube setup code: %s\n"
        "(a new code is issued at every start until setup is complete)\n%s",
        bar,
        cfg.base_url,
        code,
        bar,
    )


class TrustedProxyMiddleware:
    """Honour X-Real-IP / X-Forwarded-For / X-Forwarded-Proto only from DOWNTUBE_TRUSTED_PROXIES.

    Synology DSM's reverse proxy sends X-Real-IP and X-Forwarded-Proto. Plain-HTTP visits
    forwarded by the proxy are redirected to the HTTPS base URL.
    """

    def __init__(self, app: ASGIApp, config: Config) -> None:
        self.app = app
        self.nets = config.trusted_proxies
        self.https_base = config.base_url if config.base_url.startswith("https://") else None

    def _trusted(self, host: str | None) -> bool:
        if not host:
            return False
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            return False
        return any(addr in n for n in self.nets)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and self._trusted((scope.get("client") or (None,))[0]):
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            real = headers.get("x-real-ip") or headers.get("x-forwarded-for", "").split(",")[-1].strip()
            if real:
                try:
                    ipaddress.ip_address(real)
                    scope["client"] = (real, 0)
                except ValueError:
                    pass
            proto = headers.get("x-forwarded-proto", "").lower()
            if proto == "http" and self.https_base and scope.get("path") != "/api/health":
                qs = scope.get("query_string", b"").decode("latin-1")
                target = self.https_base + scope.get("path", "/") + (f"?{qs}" if qs else "")
                resp = RedirectResponse(target, status_code=308)
                await resp(scope, receive, send)
                return
            if proto in ("http", "https"):
                scope["scheme"] = proto
        await self.app(scope, receive, send)


def _mount_spa(app: FastAPI, static_dir: Path | None) -> None:
    """Serve the built React SPA (if present) with history-API fallback."""
    if static_dir is None or not (static_dir / "index.html").is_file():
        return
    root = static_dir.resolve()
    index = root / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(404, "not found")
        try:
            candidate = (root / full_path).resolve()
            if full_path and candidate.is_file() and candidate.is_relative_to(root):
                return FileResponse(candidate)
        except (ValueError, OSError):
            pass
        return FileResponse(index)
