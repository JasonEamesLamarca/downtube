"""0.5.0: release dates in the library (sorting, backfill from YouTube and from file tags)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from helpers import _reset_in_memory_state, fake_yt, login  # noqa: F401
from test_api import seed_video

from downtube.db import read_session, write_session
from downtube.models import Availability, GoogleAccount, MediaFile, Video
from downtube.sync import release_dates
from downtube.sync.release_dates import backfill_release_dates, parse_date_tag


def _archive(env, h, vid, *, published=None, downloaded=None, **kw):
    seed_video(h.user_id, vid, archived_file=(env["archive"], f"Alpha/{vid}.mp4"), published_at=published, **kw)
    if downloaded:
        with write_session() as s:
            s.query(MediaFile).filter(MediaFile.video_id == vid).one().created_at = downloaded


def test_library_sorts_by_release_or_download_date_unknown_last(client, env):
    h = login(client)
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    _archive(env, h, "old00000001", published=t0 - timedelta(days=900), downloaded=t0 + timedelta(days=3))
    _archive(env, h, "new00000001", published=t0, downloaded=t0 + timedelta(days=1))
    _archive(env, h, "unknown0001", published=None, downloaded=t0 + timedelta(days=2))

    def ids(q: str) -> list[str]:
        return [i["video_id"] for i in client.get(f"/api/library?{q}", headers=h).json()["items"]]

    assert ids("") == ["new00000001", "old00000001", "unknown0001"]  # default: newest release first
    assert ids("sort=published&order=asc") == ["old00000001", "new00000001", "unknown0001"]
    assert ids("sort=date&order=desc") == ids("sort=published&order=desc")  # pre-0.5 alias
    assert ids("sort=downloaded&order=desc") == ["old00000001", "unknown0001", "new00000001"]
    item = client.get("/api/library?sort=published", headers=h).json()["items"][0]
    assert item["published_at"].startswith("2026-01-01") and item["created_at"]


def test_parse_date_tag():
    assert parse_date_tag("20240115") == datetime(2024, 1, 15, tzinfo=UTC)
    assert parse_date_tag("2024-01-15T10:00:00Z") == datetime(2024, 1, 15, tzinfo=UTC)
    for bad in (None, "", "0000-00-00", "1999-01-01", "2024-13-40", "abc"):
        assert parse_date_tag(bad) is None


def test_backfill_from_youtube_then_file_tags(client, env, fake_yt, monkeypatch):
    h = login(client)
    _archive(env, h, "onyoutube01")
    _archive(env, h, "goneupstrm1")  # deleted on YouTube; file has yt-dlp's embedded date tag
    _archive(env, h, "notags00001")
    _archive(env, h, "private0001")
    with write_session() as s:
        s.get(Video, "private0001").availability = Availability.private
    seed_video(h.user_id, "likedonly01")  # not archived, still gets a date from YouTube
    _archive(env, h, "hasdate0001", published=datetime(2020, 5, 5, tzinfo=UTC))
    fake_yt.add_video("onyoutube01", published="2019-02-03T04:05:06Z")
    fake_yt.add_video("likedonly01", published="2018-01-01T00:00:00Z")

    tags = {"goneupstrm1": {"date": "20170809"}, "notags00001": {}, "private0001": {"DATE": "20160101"}}

    def fake_probe(path, binary="ffprobe", timeout=120.0):  # noqa: ARG001
        return {"format": {"tags": tags[path.stem]}}

    monkeypatch.setattr(release_dates, "ffprobe_json", fake_probe)
    monkeypatch.setattr(release_dates.paths, "media_file_path", lambda root, rel: env["archive"] / rel)

    r = client.post("/api/library/release-dates", headers=h)
    assert r.status_code == 200, r.text
    # private0001 is skipped on YouTube (known private) but its file tag still fills it in.
    assert r.json() == {
        "checked": 4,
        "from_youtube": 2,
        "from_files": 2,
        "archived_missing": 1,
        "error": None,
    }
    with read_session() as s:
        # SQLite hands back naive datetimes (stored as UTC)
        got = {v.id: v.published_at and v.published_at.replace(tzinfo=UTC) for v in s.query(Video)}
    assert got["onyoutube01"] == datetime(2019, 2, 3, 4, 5, 6, tzinfo=UTC)
    assert got["likedonly01"].year == 2018
    assert got["goneupstrm1"] == datetime(2017, 8, 9, tzinfo=UTC)
    assert got["private0001"].year == 2016
    assert got["notags00001"] is None
    assert got["hasdate0001"].year == 2020
    assert fake_yt.count("videos") == 1  # one videos.list call for all 4 (≤ 50 per call)


def test_backfill_without_account_reports_why(client, env):
    h = login(client)
    seed_video(h.user_id, "likedonly01")
    with write_session() as s:
        s.query(GoogleAccount).delete()
    res = backfill_release_dates()
    assert (res.checked, res.from_youtube, res.error) == (1, 0, "no connected Google account")
