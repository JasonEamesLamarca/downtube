"""yt-dlp downloader adapter.

Discovery/metadata (YouTube Data API) and media retrieval are deliberately separate: this
adapter is the only component that fetches media, and it does so through yt-dlp running as
a subprocess with an argument list (never a shell). It never passes cookies, geo-bypass,
or any option intended to defeat access controls, and never re-encodes video — yt-dlp's
merger performs a stream copy into the chosen container.
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from downtube.downloads.policy import Selection

log = logging.getLogger("downtube.downloader")

PROGRESS_PREFIX = "DTPROG "
POSTPROC_PREFIX = "DTPP "
OUTPUT_STEM = "media"


class DownloadError(Exception):
    def __init__(self, code: str, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class Cancelled(Exception):  # noqa: N818 - control-flow signal, not an error
    """The download was stopped by a cancel/pause/shutdown request."""


@dataclass
class Progress:
    stage: str  # downloading | merging
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    speed_bps: float | None = None
    eta_s: int | None = None
    percent: float | None = None


ProgressCallback = Callable[[Progress], None]


class DownloaderAdapter(Protocol):
    def probe(self, video_id: str) -> dict[str, Any]: ...

    def download(
        self,
        video_id: str,
        selection: Selection,
        out_dir: Path,
        *,
        rate_limit_kbps: int | None,
        write_thumbnail: bool,
        on_progress: ProgressCallback,
        cancel_event: threading.Event,
    ) -> Path: ...


# --------------------------------------------------------------------------- error classification

# (regex, code, retryable) — first match wins. Order matters.
_ERROR_RULES: list[tuple[re.Pattern[str], str, bool]] = [
    (re.compile(r"confirm you.?re not a bot", re.I), "bot_check", True),
    (re.compile(r"confirm your age|age[- ]restricted|inappropriate for some users", re.I), "age_restricted", False),
    (re.compile(r"members[- ]only|join this channel", re.I), "members_only", False),
    (re.compile(r"\bDRM\b", re.I), "drm", False),
    (
        re.compile(r"available in your country|geo[- ]?restrict|blocked it in your country", re.I),
        "geo_restricted",
        False,
    ),
    (
        re.compile(r"premieres? in|live event will begin|this live event|is_upcoming|scheduled", re.I),
        "not_yet_available",
        True,
    ),
    (
        re.compile(
            r"private video|video is private|video unavailable|has been removed|account .* terminated|"
            r"no longer available|does not exist|copyright claim",
            re.I,
        ),
        "unavailable",
        False,
    ),
    (re.compile(r"sign in|log ?in required|login_required|cookies", re.I), "login_required", False),
    (re.compile(r"no space left on device", re.I), "disk_space", True),
    (re.compile(r"HTTP Error 429|too many requests", re.I), "http_429", True),
    (re.compile(r"HTTP Error 403", re.I), "http_403", True),
    (
        re.compile(
            r"timed out|connection (reset|refused|aborted)|temporary failure|name resolution|"
            r"network is unreachable|IncompleteRead|HTTP Error 5\d\d|unable to download",
            re.I,
        ),
        "network",
        True,
    ),
    (re.compile(r"ffmpeg|postprocessing|merger", re.I), "ffmpeg_error", True),
    (re.compile(r"Requested format is not available", re.I), "format_unavailable", True),
]


def classify_error(text: str) -> tuple[str, bool]:
    for pat, code, retryable in _ERROR_RULES:
        if pat.search(text):
            return code, retryable
    return "internal", True


def _error_from_output(lines: list[str], returncode: int | None) -> DownloadError:
    errors = [ln for ln in lines if ln.startswith("ERROR:")] or lines[-5:]
    text = "\n".join(errors).strip() or f"yt-dlp exited with status {returncode}"
    code, retryable = classify_error(text)
    return DownloadError(code, text[-2000:], retryable)


# --------------------------------------------------------------------------- yt-dlp adapter


def video_url(video_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise DownloadError("invalid_id", f"invalid YouTube video id: {video_id!r}", False)
    return f"https://www.youtube.com/watch?v={video_id}"


BASE_ARGS = [
    "--ignore-config",
    "--no-playlist",
    "--no-cookies-from-browser",
    "--no-color",
    "--socket-timeout", "30",
    "--retries", "5",
    "--fragment-retries", "10",
]  # fmt: skip


class YtDlpAdapter:
    def __init__(self, binary: str = "yt-dlp", probe_timeout: float = 300.0, extra_args: list[str] | None = None):
        self.binary = binary
        self.probe_timeout = probe_timeout
        self.extra_args = list(extra_args or [])

    # -- metadata / formats
    def probe(self, video_id: str) -> dict[str, Any]:
        argv = [
            self.binary,
            *BASE_ARGS,
            *self.extra_args,
            "-J",
            "--skip-download",
            "--no-warnings",
            "--",
            video_url(video_id),
        ]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=self.probe_timeout, check=False)
        except subprocess.TimeoutExpired as e:
            raise DownloadError("network", "yt-dlp probe timed out", True) from e
        except FileNotFoundError as e:
            raise DownloadError("internal", f"yt-dlp binary not found: {self.binary}", False) from e
        if proc.returncode != 0:
            raise _error_from_output(proc.stderr.splitlines(), proc.returncode)
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise DownloadError("internal", "yt-dlp returned invalid JSON", True) from e

    # -- media
    def build_download_argv(
        self, video_id: str, selection: Selection, out_dir: Path, *, rate_limit_kbps: int | None, write_thumbnail: bool
    ) -> list[str]:
        argv = [
            self.binary, *BASE_ARGS, *self.extra_args,
            "--continue", "--no-overwrites", "--no-mtime", "--newline",
            "--progress-template", f"download:{PROGRESS_PREFIX}%(progress)j",
            "--progress-template", f"postprocess:{POSTPROC_PREFIX}%(progress.postprocessor)s %(progress.status)s",
            "-f", selection.format_spec,
            "--merge-output-format", selection.container,
            "-P", str(out_dir),
            "-o", f"{OUTPUT_STEM}.%(ext)s",
        ]  # fmt: skip
        if rate_limit_kbps:
            argv += ["--limit-rate", f"{int(rate_limit_kbps)}K"]
        if write_thumbnail:
            argv += ["--write-thumbnail", "--convert-thumbnails", "jpg", "-o", f"thumbnail:{OUTPUT_STEM}.%(ext)s"]
        argv += ["--", video_url(video_id)]
        return argv

    def download(
        self,
        video_id: str,
        selection: Selection,
        out_dir: Path,
        *,
        rate_limit_kbps: int | None,
        write_thumbnail: bool,
        on_progress: ProgressCallback,
        cancel_event: threading.Event,
    ) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        argv = self.build_download_argv(
            video_id, selection, out_dir, rate_limit_kbps=rate_limit_kbps, write_thumbnail=write_thumbnail
        )
        log.info("yt-dlp download %s format=%s container=%s", video_id, selection.format_spec, selection.container)
        try:
            proc = subprocess.Popen(  # noqa: S603 - argv list, no shell
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,  # own process group → we can stop yt-dlp *and* its ffmpeg child
            )
        except FileNotFoundError as e:
            raise DownloadError("internal", f"yt-dlp binary not found: {self.binary}", False) from e

        watcher = threading.Thread(target=self._watch_cancel, args=(proc, cancel_event), daemon=True)
        watcher.start()
        tail: deque[str] = deque(maxlen=60)
        tracker = _ProgressTracker(selection)
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            if line.startswith(PROGRESS_PREFIX):
                p = tracker.update(line[len(PROGRESS_PREFIX) :])
                if p is not None:
                    on_progress(p)
            elif line.startswith(POSTPROC_PREFIX):
                if "Merger" in line or "FFmpeg" in line:
                    on_progress(Progress(stage="merging", percent=100.0))
            elif line.strip():
                tail.append(line)
        rc = proc.wait()
        cancel_event_set = cancel_event.is_set()
        if cancel_event_set:
            raise Cancelled()
        if rc != 0:
            raise _error_from_output(list(tail), rc)
        result = find_output(out_dir, selection.container)
        if result is None:
            raise DownloadError("internal", "yt-dlp finished but no output file was found", True)
        return result

    @staticmethod
    def _watch_cancel(proc: subprocess.Popen[str], cancel_event: threading.Event) -> None:
        while proc.poll() is None:
            if cancel_event.wait(0.5):
                terminate_group(proc)
                return


def terminate_group(proc: subprocess.Popen[str], grace: float = 10.0) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def find_output(out_dir: Path, container: str) -> Path | None:
    exact = out_dir / f"{OUTPUT_STEM}.{container}"
    if exact.is_file():
        return exact
    return None


class _ProgressTracker:
    """Aggregates per-file yt-dlp progress (video then audio) into one overall figure."""

    def __init__(self, selection: Selection) -> None:
        self.expected_total = selection.estimated_bytes
        self.files: dict[str, tuple[int, int | None]] = {}

    def update(self, payload: str) -> Progress | None:
        try:
            d = json.loads(payload)
        except json.JSONDecodeError:
            return None
        if not isinstance(d, dict):
            return None
        name = str(d.get("filename") or d.get("tmpfilename") or "?")
        done = int(d.get("downloaded_bytes") or 0)
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        self.files[name] = (done, int(total) if total else None)
        downloaded = sum(v[0] for v in self.files.values())
        known_total = sum(v[1] or v[0] for v in self.files.values())
        total_bytes = max(self.expected_total or 0, known_total) or None
        pct = min(100.0, downloaded * 100.0 / total_bytes) if total_bytes else None
        speed = d.get("speed")
        eta = None
        if speed and total_bytes:
            eta = int(max(0, total_bytes - downloaded) / float(speed))
        return Progress(
            stage="downloading",
            downloaded_bytes=downloaded,
            total_bytes=total_bytes,
            speed_bps=float(speed) if speed else None,
            eta_s=eta,
            percent=round(pct, 1) if pct is not None else None,
        )
