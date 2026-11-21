from sqlalchemy import inspect

from downtube.db import get_engine


def test_health_and_migrations(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["db"] is True
    tables = set(inspect(get_engine()).get_table_names())
    assert {"videos", "download_jobs", "media_files", "channel_rules", "video_sources"} <= tables
    assert r.headers["X-Frame-Options"] == "DENY"


def test_wal_mode(client):
    with get_engine().connect() as c:
        assert c.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"


def test_crypto_roundtrip(env):
    from downtube.security.crypto import decrypt, encrypt

    tok = encrypt("1//refresh-token")
    assert tok != "1//refresh-token" and decrypt(tok) == "1//refresh-token"


def test_settings_defaults_and_update(db):
    from downtube.schemas import SettingsUpdate
    from downtube.settings_service import SettingsError, get_settings, update_settings

    st = get_settings(db)
    assert st.max_concurrent_downloads == 2 and st.min_free_gb == 100 and st.audio_preference == "aac"
    out = update_settings(db, SettingsUpdate(likes_poll_minutes=30, default_max_height=720))
    assert out.likes_poll_minutes == 30 and out.default_max_height == 720
    import pytest

    with pytest.raises(SettingsError):
        update_settings(db, SettingsUpdate(download_window="25:00-01:00"))


def test_unknown_api_route_json_error(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_redaction():
    from downtube.logging_setup import redact

    s = redact("GET /api/auth/callback?state=s&code=4/abc refresh_token=1//0gabcdefghijklmnopqrstuvwxyz Bearer ya29.x")
    assert "4/abc" not in s and "1//0g" not in s and "ya29" not in s
