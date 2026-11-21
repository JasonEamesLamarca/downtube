"""Real ffmpeg/ffprobe checks (run in CI / Docker where ffmpeg is installed)."""

import os
import shutil
import subprocess

import pytest

from downtube.downloads.policy import parse_formats, select_formats
from downtube.downloads.validator import check, ffprobe

from .yt_fixtures import audio, vfmt

FFMPEG = shutil.which("ffmpeg")
FFPROBE = os.environ.get("DOWNTUBE_FFPROBE_BIN") or shutil.which("ffprobe")
pytestmark = [pytest.mark.media, pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg/ffprobe not installed")]


def _gen(path, vcodec, acodec, size="640x360", extra=()):
    subprocess.run(
        [
            FFMPEG,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={size}:rate=30:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            vcodec,
            *extra,
            "-c:a",
            acodec,
            "-shortest",
            str(path),
        ],
        check=True,
    )


def test_h264_aac_mp4_validates(tmp_path):
    out = tmp_path / "a.mp4"
    _gen(out, "libx264", "aac")
    p = ffprobe(out, FFPROBE)
    assert (p.container, p.width, p.height, p.vcodec, p.acodec) == ("mp4", 640, 360, "h264", "aac")
    sel = select_formats(parse_formats({"formats": [vfmt("v", "avc1.4d401e", 360), audio()]}), 2160)
    assert check(p, sel, 2.0).height == 360


def test_vp9_opus_stream_copy_into_mp4(tmp_path):
    src = tmp_path / "src.webm"
    _gen(src, "libvpx-vp9", "libopus", extra=("-b:v", "200k", "-deadline", "realtime"))
    out = tmp_path / "copy.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(src), "-c", "copy", str(out)], check=True)
    p = ffprobe(out, FFPROBE)
    assert (p.container, p.vcodec, p.acodec, p.height) == ("mp4", "vp9", "opus", 360)
