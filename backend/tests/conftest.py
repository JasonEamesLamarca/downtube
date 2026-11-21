"""Shared fixtures. Feature-specific fixtures live in tests/fixtures/ and per-module conftest helpers."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TEST_SECRET = "test-secret-key-0123456789abcdef-0123456789"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    dirs = {name: tmp_path / name for name in ("data", "temp", "logs", "archive")}
    for d in dirs.values():
        d.mkdir()
    monkeypatch.setenv("DOWNTUBE_SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("DOWNTUBE_BASE_URL", "http://localhost:8092")
    monkeypatch.setenv("DOWNTUBE_ALLOWED_EMAILS", "owner@example.com,second@example.com")
    monkeypatch.setenv("DOWNTUBE_DATA_DIR", str(dirs["data"]))
    monkeypatch.setenv("DOWNTUBE_TEMP_DIR", str(dirs["temp"]))
    monkeypatch.setenv("DOWNTUBE_LOG_DIR", str(dirs["logs"]))
    monkeypatch.setenv("DOWNTUBE_STORAGE_MOUNTS", str(dirs["archive"]))
    monkeypatch.setenv("DOWNTUBE_ARCHIVE_ROOT", str(dirs["archive"]))
    monkeypatch.setenv("DOWNTUBE_DISABLE_BACKGROUND", "1")
    from downtube.config import get_config

    get_config.cache_clear()
    yield dirs
    get_config.cache_clear()


@pytest.fixture
def app(env: dict[str, Path]):  # type: ignore[no-untyped-def]
    from downtube.main import create_app

    return create_app(start_background=False)


@pytest.fixture
def client(app) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    with TestClient(app, base_url="http://localhost:8092") as c:
        yield c


@pytest.fixture
def db(client: TestClient):  # type: ignore[no-untyped-def]
    """A session bound to the initialised (migrated) test database."""
    from downtube.db import write_session

    with write_session() as s:
        yield s
