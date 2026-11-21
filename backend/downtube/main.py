"""FastAPI application factory and process lifecycle."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

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
        if background:
            from downtube import scheduler
            from downtube.downloads import manager

            manager.startup()
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
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
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
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' https://i.ytimg.com https://yt3.ggpht.com "
            "https://*.googleusercontent.com data:; media-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
        )
        return resp

    from downtube.api import api_router

    app.include_router(api_router, prefix="/api")
    _mount_spa(app, cfg.static_dir)
    return app


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
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(index)
