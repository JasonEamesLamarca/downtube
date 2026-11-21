import pytest

from downtube.downloads.adapter import DownloadError, YtDlpAdapter, classify_error, video_url
from downtube.downloads.policy import parse_formats, select_formats
from downtube.downloads.validator import Probe, ValidationError, check, parse_ffprobe

from .yt_fixtures import ladder


@pytest.mark.parametrize(
    ("msg", "code", "retry"),
    [
        ("ERROR: [youtube] x: Sign in to confirm you’re not a bot", "bot_check", True),
        ("ERROR: [youtube] x: Private video. Sign in if you've been granted access", "unavailable", False),
        ("ERROR: [youtube] x: Video unavailable. This video has been removed by the uploader", "unavailable", False),
        ("ERROR: The uploader has not made this video available in your country", "geo_restricted", False),
        ("ERROR: Sign in to confirm your age", "age_restricted", False),
        ("ERROR: Join this channel to get access to members-only content", "members_only", False),
        ("ERROR: This video is DRM protected", "drm", False),
        ("ERROR: unable to download video data: HTTP Error 429: Too Many Requests", "http_429", True),
        ("ERROR: [Errno -3] Temporary failure in name resolution", "network", True),
        ("ERROR: This live event will begin in 3 hours", "not_yet_available", True),
        ("ERROR: something odd", "internal", True),
    ],
)
def test_classify_error(msg, code, retry):
    assert classify_error(msg) == (code, retry)


def test_video_url_rejects_injection():
    with pytest.raises(DownloadError):
        video_url("--exec rm")
    assert video_url("abcdefghijk").endswith("v=abcdefghijk")


def test_download_argv_is_safe(tmp_path):
    sel = select_formats(parse_formats(ladder(2160)), 2160)
    argv = YtDlpAdapter("yt-dlp").build_download_argv(
        "abcdefghijk", sel, tmp_path, rate_limit_kbps=500, write_thumbnail=True
    )
    joined = " ".join(argv)
    assert "--ignore-config" in argv and "--no-cookies-from-browser" in argv
    assert argv[argv.index("-f") + 1] == sel.format_spec
    assert argv[argv.index("--merge-output-format") + 1] == "mp4"
    assert "--limit-rate" in argv and "500K" in argv
    for forbidden in ("--recode-video", "--cookies", "--geo-bypass", "--remux-video", "--exec", "--postprocessor-args"):
        assert forbidden not in joined
    assert argv[-2] == "--"


FFPROBE_JSON = {
    "streams": [
        {
            "codec_type": "video",
            "codec_name": "vp9",
            "width": 3840,
            "height": 2160,
            "avg_frame_rate": "60000/1001",
            "color_transfer": "bt709",
        },
        {"codec_type": "audio", "codec_name": "aac"},
    ],
    "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "60.02"},
}


def _probe(tmp_path, **over):
    f = tmp_path / "x.mp4"
    f.write_bytes(b"1")
    data = {
        "streams": [dict(FFPROBE_JSON["streams"][0], **over), FFPROBE_JSON["streams"][1]],
        "format": FFPROBE_JSON["format"],
    }
    return parse_ffprobe(data, f)


def test_parse_ffprobe(tmp_path):
    p = _probe(tmp_path)
    assert (p.container, p.width, p.height, p.vcodec, p.acodec, p.fps) == ("mp4", 3840, 2160, "vp9", "aac", 59.94)
    assert not p.is_hdr and _probe(tmp_path, color_transfer="smpte2084").is_hdr


def test_check_rejects_upscale_and_cap(tmp_path):
    sel480 = select_formats(parse_formats(ladder(480)), 2160)
    big = Probe("mp4", 1280, 720, 30, "avc1", "aac", 60.0, 10, False, "mp4")
    with pytest.raises(ValidationError, match="upscale"):
        check(big, sel480, 60)
    sel720 = select_formats(parse_formats(ladder(2160)), 720)
    huge = Probe("mp4", 3840, 2160, 30, "vp9", "aac", 60.0, 10, False, "mp4")
    with pytest.raises(ValidationError, match="exceeds"):
        check(huge, sel720, 60)


def test_check_rejects_incomplete_and_container(tmp_path):
    sel = select_formats(parse_formats(ladder(720)), 720)
    ok = Probe("mp4", 1280, 720, 30, "avc1", "aac", 60.0, 10, False, "mp4")
    assert check(ok, sel, 60.5) is ok
    with pytest.raises(ValidationError, match="duration"):
        check(Probe("mp4", 1280, 720, 30, "avc1", "aac", 30.0, 10, False, "mp4"), sel, 60)
    with pytest.raises(ValidationError, match="container"):
        check(Probe("mkv", 1280, 720, 30, "avc1", "aac", 60.0, 10, False, "matroska"), sel, 60)
    with pytest.raises(ValidationError, match="audio"):
        check(Probe("mp4", 1280, 720, 30, "avc1", None, 60.0, 10, False, "mp4"), sel, 60)


def test_parse_ffprobe_no_video(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"1")
    with pytest.raises(ValidationError):
        parse_ffprobe({"streams": [{"codec_type": "audio"}], "format": {}}, f)
