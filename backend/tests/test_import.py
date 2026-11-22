"""0.4.0: importing files the user downloaded themselves (upload + import folder)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from helpers import _reset_in_memory_state, login  # noqa: F401
from sqlalchemy import select
from test_api import seed_video

from downtube.db import read_session, write_session
from downtube.downloads import importer
from downtube.downloads.validator import Probe, ValidationError
from downtube.models import AuditEvent, DiscoverySource, DownloadJob, JobStatus, MediaFile, Video, VideoExclusion
from downtube.storage import paths

REAL_FETCH_THUMBNAIL = importer.fetch_thumbnail


def fake_probe(height=1080, duration=300.0, container=None, tags=None):
    def probe(path: Path):
        data = path.read_bytes()
        if data.startswith(b"junk"):
            raise ValidationError("ffprobe failed: invalid data")
        kind = container or ("mkv" if path.suffix in (".mkv", ".webm") else "mp4")
        return (
            Probe(
                container=kind,
                width=height * 16 // 9,
                height=height,
                fps=30.0,
                vcodec="vp9",
                acodec="aac",
                duration_s=duration,
                size_bytes=len(data),
                is_hdr=False,
                format_names="mov,mp4,m4a,3gp,3g2,mj2" if kind == "mp4" else kind,
            ),
            dict(tags or {}),
        )

    return probe


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    importer.reset_state()
    monkeypatch.setattr(importer, "probe_file", fake_probe())
    monkeypatch.setattr(importer, "fetch_thumbnail", lambda url, dest: dest.write_bytes(b"jpg") or True)
    monkeypatch.setattr(paths, "has_space_for", lambda root, needed, min_free: True)


def failed_video(h, vid="age00000001", **kw):
    seed_video(h.user_id, vid, DiscoverySource.like, duration_s=300, job=JobStatus.failed, **kw)
    with write_session() as s:
        job = s.scalar(select(DownloadJob).where(DownloadJob.video_id == vid))
        job.error_code, job.error_message = "age_restricted", "Sign in to confirm your age"
        return job.id


def upload(client, h, job_id, data=b"video-bytes", filename="clip [age00000001].mp4"):
    return client.put(f"/api/downloads/{job_id}/import", params={"filename": filename}, content=data, headers=h)


# --------------------------------------------------------------------------- upload


def test_upload_completes_failed_job_and_archives_file(client, env):
    h = login(client)
    jid = failed_video(h)
    with write_session() as s:
        s.add(VideoExclusion(user_id=h.user_id, video_id="age00000001"))
    r = upload(client, h, jid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed" and body["error_code"] is None
    assert body["selection"]["imported"] is True and body["selection"]["origin"] == "upload"
    files = sorted(p.name for p in (env["archive"] / "UCa").iterdir())
    assert files == [
        "0000-00-00 - T age00000001 [age00000001].jpg",
        "0000-00-00 - T age00000001 [age00000001].mp4",
        "0000-00-00 - T age00000001 [age00000001].nfo",
    ]
    assert (env["archive"] / "UCa" / files[1]).read_bytes() == b"video-bytes"
    assert list((env["archive"] / ".import" / ".uploading").iterdir()) == []
    with read_session() as s:
        mf = s.scalar(select(MediaFile))
        assert mf.is_current and mf.height == 1080 and mf.best_available_height == 1080
        assert s.get(VideoExclusion, (h.user_id, "age00000001")) is None
        assert s.scalar(select(AuditEvent).where(AuditEvent.action == "download.import")) is not None
    # the archive is complete: no upgrade job gets queued for it
    lib = client.get("/api/library", headers=h).json()
    assert lib["total"] == 1


@pytest.mark.parametrize(
    ("probe", "data", "code"),
    [
        (fake_probe(duration=120.0), b"v", "duration_mismatch"),
        (fake_probe(tags={"purl": "https://www.youtube.com/watch?v=other000001"}), b"v", "wrong_video"),
        (fake_probe(height=2160), b"v", "too_large"),
        (fake_probe(container="avi"), b"v", "unsupported_container"),
        (fake_probe(), b"junk", "invalid_file"),
    ],
)
def test_upload_rejections_leave_job_untouched(client, env, monkeypatch, probe, data, code):
    h = login(client)
    jid = failed_video(h)
    with write_session() as s:
        s.get(DownloadJob, jid).max_height = 1080
    monkeypatch.setattr(importer, "probe_file", probe)
    r = upload(client, h, jid, data=data)
    assert r.status_code == 422 and r.json()["error"]["code"] == code, r.text
    with read_session() as s:
        job = s.get(DownloadJob, jid)
        assert job.status == JobStatus.failed and job.error_code == "age_restricted"
        assert s.scalar(select(MediaFile)) is None
    assert list((env["archive"] / ".import" / ".uploading").iterdir()) == []


def test_upload_checks_owner_name_and_existing_archive(client, env):
    h = login(client)
    jid = failed_video(h)
    other = login(client, "second@example.com")
    assert upload(client, other, jid).status_code == 404
    r = upload(client, h, jid, filename="x [zzzzzzzzzzz].mp4")
    assert r.status_code == 422 and r.json()["error"]["code"] == "wrong_video"
    assert upload(client, h, jid, data=b"").json()["error"]["code"] == "empty_upload"
    assert upload(client, h, jid).status_code == 200
    r = upload(client, h, jid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_archived"


def test_upload_requires_csrf(client, env):
    h = login(client)
    jid = failed_video(h)
    r = client.put(f"/api/downloads/{jid}/import", content=b"v", headers={"Cookie": h["Cookie"]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"


def test_upload_refused_when_disk_is_full(client, env, monkeypatch):
    h = login(client)
    jid = failed_video(h)
    monkeypatch.setattr(paths, "has_space_for", lambda root, needed, min_free: False)
    r = upload(client, h, jid)
    assert r.status_code == 507 and r.json()["error"]["code"] == "disk_space"


# --------------------------------------------------------------------------- import folder


def _drop(env, name: str, data: bytes = b"folder-bytes", age: float = 120) -> Path:
    d = env["archive"] / ".import"
    d.mkdir(exist_ok=True)
    f = d / name
    f.write_bytes(data)
    old = time.time() - age
    os.utime(f, (old, old))
    return f


def test_folder_scan_imports_matching_files_and_reports_problems(client, env):
    h = login(client)
    failed_video(h)
    seed_video(h.user_id, "fold0000002", DiscoverySource.subscription, duration_s=300)  # no job yet
    ok1 = _drop(env, "Some title [age00000001].mp4")
    ok2 = _drop(env, "Other [fold0000002].mkv")
    noid = _drop(env, "no id here.mp4")
    unknown = _drop(env, "x [unknown0001].mp4")
    fresh = _drop(env, "copying [fold0000003].mp4", age=0)

    r = client.post("/api/imports/scan", headers=h)
    assert r.status_code == 200, r.text
    by_name = {f["name"]: f for f in r.json()["files"]}
    assert by_name[ok1.name]["status"] == "imported" and by_name[ok2.name]["status"] == "imported"
    assert by_name[noid.name]["status"] == "failed" and "brackets" in by_name[noid.name]["message"]
    assert by_name[unknown.name]["status"] == "failed" and "not in DownTube" in by_name[unknown.name]["message"]
    assert by_name[fresh.name]["status"] == "waiting"
    assert not ok1.exists() and not ok2.exists() and noid.exists()
    assert r.json()["path"].endswith("/.import")
    assert (env["archive"] / ".import" / ".plexignore").read_text() == "*\n"

    with read_session() as s:
        jobs = {j.video_id: j for j in s.scalars(select(DownloadJob))}
        assert jobs["age00000001"].status == JobStatus.completed
        assert jobs["fold0000002"].status == JobStatus.completed  # created for the import
        assert jobs["fold0000002"].selection["origin"] == "folder"
        assert {m.video_id for m in s.scalars(select(MediaFile))} == {"age00000001", "fold0000002"}
        assert s.scalar(select(MediaFile).where(MediaFile.video_id == "fold0000002")).rel_path.endswith(".mkv")

    listing = client.get("/api/imports", headers=h).json()["files"]
    assert {f["name"]: f["status"] for f in listing} == {
        noid.name: "failed",
        unknown.name: "failed",
        fresh.name: "waiting",
    }


def test_folder_scan_skips_running_job_and_retries_changed_files(client, env, monkeypatch):
    h = login(client)
    failed_video(h)
    with write_session() as s:
        s.scalar(select(DownloadJob)).status = JobStatus.running
    f = _drop(env, "a [age00000001].mp4")
    res = importer.scan_folder()
    assert res[0].status == "failed" and "downloading right now" in (res[0].message or "")
    calls = []
    real = importer.import_file
    monkeypatch.setattr(importer, "import_file", lambda *a, **k: calls.append(1) or real(*a, **k))
    importer.scan_folder()  # unchanged file: not retried by the periodic scan
    assert calls == []
    importer.scan_folder(force=True)  # "Scan now" retries
    assert calls == [1] and f.exists()


def test_video_id_from_name_and_tags():
    assert importer.video_id_from_name("2024 - T [abc] [dQw4w9WgXcQ].mp4") == "dQw4w9WgXcQ"
    assert importer.video_id_from_name("nothing.mp4") is None
    assert importer.tagged_video_ids({"comment": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}) == {"dQw4w9WgXcQ"}
    assert importer.tagged_video_ids({"description": "see youtu.be/aaaaaaaaaaa"}) == set()


def test_thumbnail_fetch_only_from_youtube_hosts(tmp_path):
    assert REAL_FETCH_THUMBNAIL("https://evil.example.com/x.jpg", tmp_path / "t.jpg") is False
    assert REAL_FETCH_THUMBNAIL("https://i.ytimg.com.evil.example/x.jpg", tmp_path / "t.jpg") is False
    assert REAL_FETCH_THUMBNAIL(None, tmp_path / "t.jpg") is False


def test_title_in_video_is_used_for_names(client, env):
    h = login(client)
    jid = failed_video(h)
    with write_session() as s:
        s.get(Video, "age00000001").title = "Ünïcode: title/with?chars"
    assert upload(client, h, jid).status_code == 200
    names = [p.name for p in (env["archive"] / "UCa").iterdir() if p.suffix == ".mp4"]
    assert names == ["0000-00-00 - Ünïcode title with chars [age00000001].mp4"]
