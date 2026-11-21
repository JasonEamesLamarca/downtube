"""First-run setup, in-app Google settings, trusted-proxy handling (v0.2)."""

from __future__ import annotations

import logging
import re

import pytest
from helpers import _reset_in_memory_state, login  # noqa: F401
from sqlalchemy import select, text

from downtube.db import get_engine, read_session

CID = "1234-abc.apps.googleusercontent.com"
SECRET = "GOCSPX-test-secret-value"


@pytest.fixture
def setup_env(env, monkeypatch):
    """No Google/email config in the environment → setup mode."""
    for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "DOWNTUBE_ALLOWED_EMAILS", "DOWNTUBE_ADMIN_EMAILS"):
        monkeypatch.delenv(k, raising=False)
    from downtube.config import get_config

    get_config.cache_clear()
    from downtube.api.setup import setup_limiter

    setup_limiter.reset()
    return env


@pytest.fixture
def setup_client(setup_env, caplog):
    from fastapi.testclient import TestClient

    from downtube.main import create_app

    caplog.set_level(logging.WARNING, logger="downtube")
    with TestClient(create_app(start_background=False), base_url="http://localhost:8092") as c:
        m = re.search(r"DownTube setup code: ([A-Z0-9-]+)", caplog.text)
        assert m, "setup code must be logged at startup"
        c.setup_code = m.group(1)  # type: ignore[attr-defined]
        yield c


def body(code, **over):
    b = {
        "setup_code": code,
        "google_client_id": CID,
        "google_client_secret": SECRET,
        "allowed_emails": ["Owner@Example.com", "friend@example.com"],
        "admin_emails": ["owner@example.com"],
    }
    b.update(over)
    return b


def test_status_needs_setup_and_login_blocked(setup_client):
    st = setup_client.get("/api/setup/status").json()
    assert st["needs_setup"] and not st["google_env_managed"]
    assert st["redirect_uri"] == "http://localhost:8092/api/auth/callback"
    r = setup_client.get("/api/auth/login", follow_redirects=False)
    assert r.status_code == 503 and r.json()["error"]["code"] == "oauth_not_configured"


def test_wrong_code_rejected_and_counted(setup_client):
    r = setup_client.post("/api/setup", json=body("AAAA-BBBB-CCCC"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "invalid_setup_code"
    with read_session() as s:
        from downtube.models import Setting

        assert s.get(Setting, "setup_code").value["failures"] == 1  # persisted despite the error


def test_setup_success_stores_encrypted_and_is_single_use(setup_client):
    code = setup_client.setup_code.lower().replace("-", " ")  # tolerant input
    r = setup_client.post("/api/setup", json=body(code))
    assert r.status_code == 200, r.text
    assert r.json()["needs_setup"] is False
    from downtube import app_config

    creds = app_config.google_creds()
    assert creds and creds.client_id == CID and creds.client_secret == SECRET
    assert app_config.allowed_emails() == {"owner@example.com", "friend@example.com"}
    with get_engine().connect() as c:
        dump = " ".join(str(r) for r in c.execute(text("SELECT value FROM settings")))
    assert SECRET not in dump  # encrypted at rest
    again = setup_client.post("/api/setup", json=body(setup_client.setup_code))
    assert again.status_code == 409
    r = setup_client.get("/api/auth/login", follow_redirects=False)
    assert r.status_code == 302 and f"client_id={CID}" in r.headers["location"]


@pytest.mark.parametrize(
    ("over", "frag"),
    [
        ({"google_client_id": "not-a-client"}, "googleusercontent"),
        ({"allowed_emails": []}, "at least one"),
        ({"allowed_emails": ["bad"]}, "valid email"),
        ({"admin_emails": ["other@example.com"]}, "administrator"),
    ],
)
def test_setup_validation(setup_client, over, frag):
    r = setup_client.post("/api/setup", json=body(setup_client.setup_code, **over))
    assert r.status_code == 422 and frag in r.json()["error"]["message"]
    assert setup_client.get("/api/setup/status").json()["needs_setup"] is True


def test_too_many_failures_kill_code(setup_client):
    from downtube import app_config
    from downtube.api.setup import setup_limiter

    for _ in range(app_config.MAX_SETUP_FAILURES):
        setup_limiter.reset()
        setup_client.post("/api/setup", json=body("WRONG-CODE-0000"))
    setup_limiter.reset()
    r = setup_client.post("/api/setup", json=body(setup_client.setup_code))
    assert r.status_code == 403 and "restart" in r.json()["error"]["message"]


def test_setup_rate_limited(setup_client):
    codes = [setup_client.post("/api/setup", json=body("WRONG-CODE-0000")).status_code for _ in range(12)]
    assert 429 in codes


def test_env_configured_means_no_setup(client):
    st = client.get("/api/setup/status").json()
    assert st["needs_setup"] is False and st["google_env_managed"] and st["emails_env_managed"]
    r = client.post("/api/setup", json=body("AAAA-BBBB-CCCC"))
    assert r.status_code == 409


def test_admin_google_settings_in_db_mode(setup_client):
    setup_client.post("/api/setup", json=body(setup_client.setup_code))
    admin = login(setup_client, "owner@example.com", admin=True)
    friend = login(setup_client, "friend@example.com", admin=False)
    assert setup_client.get("/api/admin/google", headers=friend).status_code == 403
    g = setup_client.get("/api/admin/google", headers=admin).json()
    assert g["google_client_id"] == CID and g["google_client_secret_set"] and "google_client_secret" not in g
    # cannot lock yourself out
    r = setup_client.put("/api/admin/google", headers=admin, json={"allowed_emails": ["friend@example.com"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "cannot_remove_self"
    # promote friend, rotate secret
    r = setup_client.put(
        "/api/admin/google",
        headers=admin,
        json={
            "admin_emails": ["owner@example.com", "friend@example.com"],
            "google_client_secret": "GOCSPX-rotated-secret",
        },
    )
    assert r.status_code == 200
    from downtube import app_config
    from downtube.models import User

    assert app_config.google_creds().client_secret == "GOCSPX-rotated-secret"
    with read_session() as s:
        assert s.scalar(select(User).where(User.email == "friend@example.com")).is_admin
    # removing friend from the allowlist revokes their session
    setup_client.put(
        "/api/admin/google",
        headers=admin,
        json={"allowed_emails": ["owner@example.com"], "admin_emails": ["owner@example.com"]},
    )
    assert setup_client.get("/api/auth/me", headers=friend).status_code == 401


def test_admin_google_env_managed_is_read_only(client):
    admin = login(client, "owner@example.com", admin=True)
    r = client.put("/api/admin/google", headers=admin, json={"google_client_id": CID})
    assert r.status_code == 409 and r.json()["error"]["code"] == "env_managed"


def test_docs_require_login(client):
    assert client.get("/api/docs").status_code == 401
    assert client.get("/api/openapi.json").status_code == 401
    h = login(client, "owner@example.com")
    assert client.get("/api/openapi.json", headers=h).json()["info"]["title"] == "DownTube"
    assert "swagger" in client.get("/api/docs", headers=h).text.lower()


# --------------------------------------------------------------------------- trusted proxy / https


@pytest.fixture
def proxied_client(env, monkeypatch):
    from fastapi.testclient import TestClient

    from downtube.config import get_config
    from downtube.main import create_app

    monkeypatch.setenv("DOWNTUBE_BASE_URL", "https://downtube.example.com")
    monkeypatch.setenv("DOWNTUBE_TRUSTED_PROXIES", "127.0.0.0/8")
    get_config.cache_clear()
    with TestClient(
        create_app(start_background=False), base_url="https://downtube.example.com", client=("127.0.0.1", 50000)
    ) as c:
        yield c


def test_hsts_and_http_redirect(proxied_client):
    r = proxied_client.get("/api/setup/status", headers={"X-Forwarded-Proto": "https"})
    assert r.headers["Strict-Transport-Security"].startswith("max-age=")
    r = proxied_client.get("/library?x=1", headers={"X-Forwarded-Proto": "http"}, follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == "https://downtube.example.com/library?x=1"


def test_real_ip_from_trusted_proxy_used_for_rate_limit(proxied_client):
    from downtube.auth.ratelimit import auth_limiter

    auth_limiter.reset()
    for i in range(25):  # 25 distinct clients behind the proxy: none is limited
        r = proxied_client.get(
            "/api/auth/login",
            headers={"X-Real-IP": f"203.0.113.{i}", "X-Forwarded-Proto": "https"},
            follow_redirects=False,
        )
        assert r.status_code == 302
    codes = [
        proxied_client.get("/api/auth/login", headers={"X-Real-IP": "198.51.100.7"}, follow_redirects=False).status_code
        for _ in range(25)
    ]
    assert 429 in codes


def test_untrusted_peer_cannot_spoof_ip(env, monkeypatch):
    from fastapi.testclient import TestClient

    from downtube.auth.ratelimit import auth_limiter
    from downtube.config import get_config
    from downtube.main import create_app

    monkeypatch.setenv("DOWNTUBE_TRUSTED_PROXIES", "10.9.9.9/32")
    get_config.cache_clear()
    auth_limiter.reset()
    with TestClient(create_app(start_background=False), client=("192.0.2.50", 1)) as c:
        codes = [
            c.get("/api/auth/login", headers={"X-Real-IP": f"203.0.113.{i}"}, follow_redirects=False).status_code
            for i in range(25)
        ]
    assert 429 in codes  # spoofed header ignored: all requests count against the real peer
