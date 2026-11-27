import pytest

from downtube.downloads.policy import Selection, Unsupported, best_available_height, parse_formats, select_formats

from .yt_fixtures import audio, ladder, vfmt


def sel(info, cap, **kw) -> Selection:
    r = select_formats(parse_formats(info), cap, **kw)
    assert isinstance(r, Selection), r
    return r


# Mandatory resolution table (project specification): never upscale, never exceed the cap.
@pytest.mark.parametrize(
    ("source", "limit", "expected"),
    [
        (480, 2160, 480),
        (720, 2160, 720),
        (1080, 2160, 1080),
        (2160, 2160, 2160),
        (2160, 720, 720),
        (4320, 2160, 2160),
        (1440, 2160, 1440),
        (1440, 1080, 1080),
        (360, 1080, 360),
        (2160, None, 2160),  # "best available" is still hard-capped at 4K
        (4320, None, 2160),
    ],
)
def test_resolution_table(source, limit, expected):
    s = sel(ladder(source), limit)
    assert s.height == expected
    assert s.height <= min(limit or 2160, 2160)
    assert s.height <= source  # never upscale
    assert s.container == "mp4"


def test_8k_never_selected_even_if_cap_requested_higher():
    s = sel(ladder(4320), 4320)
    assert s.effective_cap == 2160 and s.height == 2160
    assert any("above the limit" in r for r in s.reasons)


def test_portrait_uses_short_side():
    s = sel(ladder(2160, portrait=True), 1080)
    assert (s.video.width, s.video.height) == (1080, 1920)


def test_prefers_60fps_at_same_resolution():
    info = ladder(1080)
    info["formats"].append(vfmt("299", "avc1.64002a", 1080, fps=60))
    assert sel(info, 1080).video.format_id == "299"


def test_codec_preference_never_lowers_resolution():
    # H.264 tops out at 1080p; the H.264-first ranking must still pick the 4K VP9/AV1 stream.
    s = sel(ladder(2160), 2160)
    assert s.height == 2160 and s.video.vfamily in ("vp9", "av1")


def test_prefers_h264_at_equal_resolution():
    for cap in (1080, 720, 480):
        assert sel(ladder(1080), cap).video.vfamily == "avc1"


def test_vp9_before_av1_when_no_h264():
    info = ladder(2160)
    s = sel(info, 2160)
    assert s.video.vfamily == "vp9"


def test_audio_preference_aac_default_and_opus_option():
    assert sel(ladder(1080), 1080).audio.format_id == "140"
    assert sel(ladder(1080), 1080, audio_pref="opus").audio.format_id == "251"


def test_audio_falls_back_when_preferred_missing():
    info = ladder(720)
    info["formats"] = [f for f in info["formats"] if not str(f["acodec"]).startswith("mp4a")]
    s = sel(info, 720, audio_pref="aac")
    assert s.audio.afamily == "opus" and any("unavailable" in r for r in s.reasons)


def test_original_language_audio_first():
    info = ladder(720)
    info["formats"] += [
        audio("140-dub", abr=160, language="es", format_note="Spanish"),
        audio("140-orig", abr=120, language="en", format_note="English original (default)", language_preference=10),
    ]
    assert sel(info, 720).audio.format_id == "140-orig"


def test_hdr_skipped_by_default_and_preferred_on_request():
    info = ladder(2160, with_hdr=True)
    assert not sel(info, 2160).video.is_hdr
    assert sel(info, 2160, prefer_hdr=True).video.is_hdr


def test_only_hdr_available_is_still_taken():
    info = {"formats": [vfmt("hdr", "vp09.02.51.10", 2160, hdr=True), audio()]}
    assert sel(info, 2160).video.is_hdr


def test_drm_and_storyboards_excluded():
    info = ladder(720)
    info["formats"].append(vfmt("drm", "avc1.640028", 1080, has_drm=True))
    s = sel(info, 2160)
    assert s.height == 720 and s.video.format_id != "drm"


def test_combined_stream_used_only_when_nothing_better():
    info = {
        "formats": [
            {
                "format_id": "18",
                "ext": "mp4",
                "vcodec": "avc1.42001E",
                "acodec": "mp4a.40.2",
                "width": 640,
                "height": 360,
                "fps": 30,
            }
        ]
    }
    s = sel(info, 2160)
    assert s.video.format_id == "18" and s.audio is None and s.format_spec == "18"


def test_incompatible_source_unsupported_without_fallback():
    info = {"formats": [vfmt("vp8", "vp8", 720), audio("vorb", "vorbis", 128)]}
    r = select_formats(parse_formats(info), 2160)
    assert isinstance(r, Unsupported) and r.code == "no_mp4_combination" and r.source_best_height == 720


def test_incompatible_source_mkv_fallback_keeps_resolution():
    info = {"formats": [vfmt("vp8", "vp8", 720), vfmt("avc", "avc1.4d401e", 480), audio("vorb", "vorbis", 128)]}
    s = sel(info, 2160, mkv_fallback=True)
    assert s.container == "mkv" and s.height == 720  # did not drop to the 480p H.264 stream


def test_same_resolution_alternative_preferred_over_mkv():
    info = {"formats": [vfmt("vp8", "vp8", 720, tbr=5000), vfmt("avc", "avc1.4d401f", 720), audio()]}
    s = sel(info, 2160, mkv_fallback=True)
    assert s.container == "mp4" and s.video.format_id == "avc"


def test_cap_below_every_stream():
    info = {"formats": [vfmt("x", "avc1", 720), audio()]}
    r = select_formats(parse_formats(info), 360)
    assert isinstance(r, Unsupported) and r.code == "no_stream_within_limit"


def test_best_available_height_and_roundtrip():
    fmts = parse_formats(ladder(4320))
    assert best_available_height(fmts) == 2160
    assert best_available_height(fmts, 1080) == 1080
    s = sel(ladder(1080), 2160)
    assert Selection.from_dict(s.to_dict()).format_spec == s.format_spec
