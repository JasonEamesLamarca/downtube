"""Media validation with ffprobe. A job may only complete after ``validate`` passes."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from downtube.downloads.policy import Selection

HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
CONTAINER_NAMES = {"mp4": ("mov", "mp4", "m4a", "3gp", "3g2", "mj2"), "mkv": ("matroska", "webm")}


class ValidationError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class Probe:
    container: str
    width: int
    height: int
    fps: float | None
    vcodec: str | None
    acodec: str | None
    duration_s: float | None
    size_bytes: int
    is_hdr: bool
    format_names: str

    @property
    def short_side(self) -> int:
        return min(self.width, self.height)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fps(rate: str | None) -> float | None:
    if not rate or rate in ("0/0", "0"):
        return None
    try:
        if "/" in rate:
            n, d = rate.split("/", 1)
            return round(float(n) / float(d), 3) if float(d) else None
        return round(float(rate), 3)
    except ValueError:
        return None


def parse_ffprobe(data: dict[str, Any], path: Path) -> Probe:
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = next(
        (s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")),
        None,
    )
    if video is None:
        raise ValidationError("no video stream found")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if not width or not height:
        raise ValidationError("video stream has no dimensions")
    duration = fmt.get("duration") or video.get("duration")
    names = str(fmt.get("format_name") or "")
    container = (
        "mp4"
        if any(n in names.split(",") for n in CONTAINER_NAMES["mp4"])
        else ("mkv" if any(n in names.split(",") for n in CONTAINER_NAMES["mkv"]) else names)
    )
    return Probe(
        container=container,
        width=width,
        height=height,
        fps=_fps(video.get("avg_frame_rate")) or _fps(video.get("r_frame_rate")),
        vcodec=video.get("codec_name"),
        acodec=audio.get("codec_name") if audio else None,
        duration_s=float(duration) if duration else None,
        size_bytes=path.stat().st_size,
        is_hdr=(video.get("color_transfer") or "") in HDR_TRANSFERS,
        format_names=names,
    )


def ffprobe(path: Path, binary: str = "ffprobe", timeout: float = 120.0) -> Probe:
    if not path.is_file():
        raise ValidationError("output file does not exist")
    if path.stat().st_size == 0:
        raise ValidationError("output file is empty")
    argv = [binary, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", "--", str(path)]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as e:
        raise ValidationError(f"ffprobe binary not found: {binary}") from e
    except subprocess.TimeoutExpired as e:
        raise ValidationError("ffprobe timed out") from e
    if proc.returncode != 0:
        raise ValidationError(f"ffprobe failed: {proc.stderr.strip()[-500:]}")
    try:
        return parse_ffprobe(json.loads(proc.stdout), path)
    except json.JSONDecodeError as e:
        raise ValidationError("ffprobe returned invalid JSON") from e


def check(probe: Probe, selection: Selection, expected_duration_s: float | None) -> Probe:
    """Enforce the archive rules on an already-probed file."""
    if probe.size_bytes <= 0:
        raise ValidationError("output file is empty")
    if probe.container != selection.container:
        raise ValidationError(f"container is {probe.format_names!r}, expected {selection.container}")
    if probe.short_side > selection.effective_cap:
        raise ValidationError(f"resolution {probe.short_side}p exceeds the configured limit {selection.effective_cap}p")
    source_short = selection.height
    if source_short and probe.short_side > source_short:
        raise ValidationError(
            f"output resolution {probe.short_side}p is larger than the source stream {source_short}p (upscale refused)"
        )
    if selection.audio is not None and probe.acodec is None:
        raise ValidationError("audio stream missing from output")
    if expected_duration_s and probe.duration_s is not None:
        tolerance = max(2.0, expected_duration_s * 0.02)
        if abs(probe.duration_s - expected_duration_s) > tolerance:
            raise ValidationError(
                f"duration {probe.duration_s:.1f}s differs from expected {expected_duration_s:.1f}s (incomplete file?)"
            )
    elif expected_duration_s and probe.duration_s is None:
        raise ValidationError("output duration could not be determined")
    return probe


def validate(path: Path, selection: Selection, expected_duration_s: float | None, binary: str = "ffprobe") -> Probe:
    return check(ffprobe(path, binary), selection, expected_duration_s)
