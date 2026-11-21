"""Realistic yt-dlp ``info`` dicts for YouTube-like format ladders (no network)."""

from __future__ import annotations

from typing import Any

# (short side, itag avc1, itag vp9, itag av01)
LADDER = [
    (144, "160", "278", "394"),
    (240, "133", "242", "395"),
    (360, "134", "243", "396"),
    (480, "135", "244", "397"),
    (720, "136", "247", "398"),
    (1080, "137", "248", "399"),
    (1440, None, "271", "400"),
    (2160, None, "313", "401"),
    (4320, None, None, "571"),
]
LONG = {144: 256, 240: 426, 360: 640, 480: 854, 720: 1280, 1080: 1920, 1440: 2560, 2160: 3840, 4320: 7680}


def vfmt(
    fid: str,
    codec: str,
    h: int,
    *,
    fps: float = 30,
    portrait: bool = False,
    hdr: bool = False,
    tbr: float | None = None,
    **extra: Any,
) -> dict[str, Any]:
    w, hh = LONG[h], h
    if portrait:
        w, hh = h, LONG[h]
    return {
        "format_id": fid,
        "ext": "mp4" if codec.startswith(("avc1", "av01")) else "webm",
        "vcodec": codec,
        "acodec": "none",
        "width": w,
        "height": hh,
        "fps": fps,
        "tbr": tbr or h * 2.5,
        "vbr": tbr or h * 2.5,
        "filesize": h * 10_000,
        "dynamic_range": "HDR10" if hdr else "SDR",
        "protocol": "https",
        **extra,
    }


def audio(fid: str = "140", codec: str = "mp4a.40.2", abr: float = 129.5, **extra: Any) -> dict[str, Any]:
    return {
        "format_id": fid,
        "ext": "m4a" if codec.startswith("mp4a") else "webm",
        "vcodec": "none",
        "acodec": codec,
        "abr": abr,
        "tbr": abr,
        "filesize": 5_000_000,
        "protocol": "https",
        **extra,
    }


def ladder(
    max_h: int,
    *,
    fps: float = 30,
    portrait: bool = False,
    codecs: tuple[str, ...] = ("avc1", "vp9", "av01"),
    with_hdr: bool = False,
) -> dict[str, Any]:
    fmts: list[dict[str, Any]] = [
        {
            "format_id": "sb0",
            "ext": "mhtml",
            "vcodec": "none",
            "acodec": "none",
            "format_note": "storyboard",
            "protocol": "mhtml",
        },
        {
            "format_id": "18",
            "ext": "mp4",
            "vcodec": "avc1.42001E",
            "acodec": "mp4a.40.2",
            "width": 640,
            "height": 360,
            "fps": 30,
            "tbr": 500,
            "protocol": "https",
        },
        audio("139", abr=48.8),
        audio("140"),
        audio("249", "opus", 50.0),
        audio("251", "opus", 135.0),
    ]
    for h, avc, vp9, av1 in LADDER:
        if h > max_h:
            break
        if avc and "avc1" in codecs:
            fmts.append(vfmt(avc, "avc1.640028", h, fps=fps, portrait=portrait))
        if vp9 and "vp9" in codecs:
            fmts.append(vfmt(vp9, "vp9", h, fps=fps, portrait=portrait))
            if with_hdr and h >= 720:
                fmts.append(vfmt(f"{vp9}-hdr", "vp09.02.51.10", h, fps=fps, portrait=portrait, hdr=True))
        if av1 and "av01" in codecs:
            fmts.append(vfmt(av1, "av01.0.08M.08", h, fps=fps, portrait=portrait))
    return {"id": "abcdefghijk", "title": "Test", "duration": 60, "formats": fmts}
