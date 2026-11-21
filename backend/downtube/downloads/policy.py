"""Format-selection policy — pure functions, no I/O.

Absolute rules (see docs/architecture/CONTRACTS.md §5):

* The resolution cap is ``min(requested or 2160, 2160)`` measured on the SHORT side,
  so portrait 1080x1920 counts as 1080p. Nothing above the cap is ever selected (no 8K).
* The highest original resolution within the cap always wins. Codec, HDR and audio
  preferences only break ties *at that resolution* — resolution is never traded for codec.
* DownTube never upscales and never re-encodes video: the chosen streams are muxed with
  stream copy. Validation (``validator.py``) rejects any output taller than its source.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

MAX_SHORT_SIDE = 2160

MP4_VIDEO = ("avc1", "avc3", "h264", "hev1", "hvc1", "hevc", "h265", "vp09", "vp9", "av01", "av1")
MP4_AUDIO = ("mp4a", "aac", "opus", "ac-3", "ac3", "ec-3", "eac3", "flac", "mp3")
NOT_A_STREAM_EXT = ("mhtml",)


@dataclass(frozen=True)
class Fmt:
    format_id: str
    ext: str = ""
    vcodec: str = "none"
    acodec: str = "none"
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    tbr: float | None = None
    abr: float | None = None
    vbr: float | None = None
    filesize: int | None = None
    dynamic_range: str | None = None
    language: str | None = None
    language_preference: int | None = None
    format_note: str | None = None
    protocol: str | None = None
    has_drm: bool = False

    @property
    def has_video(self) -> bool:
        return bool(self.vcodec) and self.vcodec != "none"

    @property
    def has_audio(self) -> bool:
        return bool(self.acodec) and self.acodec != "none"

    @property
    def is_hdr(self) -> bool:
        return bool(self.dynamic_range) and self.dynamic_range.upper() != "SDR"

    @property
    def vfamily(self) -> str:
        return codec_family(self.vcodec)

    @property
    def afamily(self) -> str:
        return codec_family(self.acodec)

    def label(self) -> str:
        if self.has_video:
            fps = f"{self.fps:g}fps " if self.fps else ""
            hdr = " HDR" if self.is_hdr else ""
            return f"{self.format_id} {self.width}x{self.height} {fps}{self.vfamily}{hdr}"
        return f"{self.format_id} {self.afamily} {self.abr or self.tbr or '?'}k"


@dataclass
class Selection:
    video: Fmt
    audio: Fmt | None
    container: Literal["mp4", "mkv"]
    effective_cap: int
    source_best_height: int
    reasons: list[str] = field(default_factory=list)

    @property
    def format_spec(self) -> str:
        return self.video.format_id + (f"+{self.audio.format_id}" if self.audio else "")

    @property
    def height(self) -> int:
        return short_side(self.video)

    @property
    def estimated_bytes(self) -> int | None:
        sizes = [f.filesize for f in (self.video, self.audio) if f is not None]
        if any(sz is None for sz in sizes):
            return None
        return sum(sz for sz in sizes if sz is not None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_spec": self.format_spec,
            "container": self.container,
            "effective_cap": self.effective_cap,
            "source_best_height": self.source_best_height,
            "height": self.height,
            "video": asdict(self.video),
            "audio": asdict(self.audio) if self.audio else None,
            "estimated_bytes": self.estimated_bytes,
            "reasons": self.reasons,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Selection:
        return cls(
            video=Fmt(**d["video"]),
            audio=Fmt(**d["audio"]) if d.get("audio") else None,
            container=d["container"],
            effective_cap=d["effective_cap"],
            source_best_height=d["source_best_height"],
            reasons=list(d.get("reasons", [])),
        )


@dataclass
class Unsupported:
    reason: str
    code: str = "no_mp4_combination"
    source_best_height: int | None = None


# ----------------------------------------------------------------------------- helpers


def codec_family(codec: str | None) -> str:
    c = (codec or "none").lower()
    for prefix, fam in (
        ("avc", "avc1"), ("h264", "avc1"),
        ("hev", "hevc"), ("hvc", "hevc"), ("h265", "hevc"),
        ("vp09", "vp9"), ("vp9", "vp9"), ("vp8", "vp8"),
        ("av01", "av1"), ("av1", "av1"),
        ("mp4a", "aac"), ("aac", "aac"), ("opus", "opus"), ("vorbis", "vorbis"),
        ("ac-3", "ac3"), ("ac3", "ac3"), ("ec-3", "eac3"), ("eac3", "eac3"),
        ("flac", "flac"), ("mp3", "mp3"),
    ):  # fmt: skip
        if c.startswith(prefix):
            return fam
    return c


def video_mp4_ok(f: Fmt) -> bool:
    return f.vfamily in {codec_family(c) for c in MP4_VIDEO}


def audio_mp4_ok(f: Fmt) -> bool:
    return f.afamily in {codec_family(c) for c in MP4_AUDIO}


def effective_cap(requested: int | None) -> int:
    if requested is None:
        return MAX_SHORT_SIDE
    return max(1, min(int(requested), MAX_SHORT_SIDE))


def short_side(f: Fmt) -> int:
    dims = [d for d in (f.width, f.height) if d]
    return min(dims) if dims else 0


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def parse_formats(info: dict[str, Any]) -> list[Fmt]:
    out: list[Fmt] = []
    for raw in info.get("formats") or []:
        fid = raw.get("format_id")
        if not fid:
            continue
        fs = raw.get("filesize") or raw.get("filesize_approx")
        out.append(
            Fmt(
                format_id=str(fid),
                ext=raw.get("ext") or "",
                vcodec=raw.get("vcodec") or "none",
                acodec=raw.get("acodec") or "none",
                width=raw.get("width"),
                height=raw.get("height"),
                fps=_num(raw.get("fps")),
                tbr=_num(raw.get("tbr")),
                abr=_num(raw.get("abr")),
                vbr=_num(raw.get("vbr")),
                filesize=int(fs) if fs else None,
                dynamic_range=raw.get("dynamic_range"),
                language=raw.get("language"),
                language_preference=raw.get("language_preference"),
                format_note=raw.get("format_note"),
                protocol=raw.get("protocol"),
                has_drm=bool(raw.get("has_drm")),
            )
        )
    return out


def _usable(f: Fmt) -> bool:
    if f.has_drm or f.ext in NOT_A_STREAM_EXT:
        return False
    if (f.protocol or "").startswith("mhtml") or (f.format_note or "").lower().startswith("storyboard"):
        return False
    return f.has_video or f.has_audio


def _video_candidates(fmts: list[Fmt]) -> list[Fmt]:
    return [f for f in fmts if _usable(f) and f.has_video and short_side(f) > 0]


def best_available_height(fmts: list[Fmt], cap: int = MAX_SHORT_SIDE) -> int | None:
    heights = [short_side(f) for f in _video_candidates(fmts) if short_side(f) <= effective_cap(cap)]
    return max(heights) if heights else None


def summarize_formats(fmts: list[Fmt]) -> list[dict[str, Any]]:
    """Compact, storable summary of the original formats (for the DB / UI)."""
    return [
        {
            "id": f.format_id,
            "v": f.vfamily if f.has_video else None,
            "a": f.afamily if f.has_audio else None,
            "w": f.width,
            "h": f.height,
            "fps": f.fps,
            "hdr": f.is_hdr or None,
            "kbps": round(f.tbr) if f.tbr else None,
            "size": f.filesize,
        }
        for f in fmts
        if _usable(f)
    ]


# ----------------------------------------------------------------------------- selection

_CODEC_RANK_COMPAT = {"avc1": 0, "vp9": 1, "av1": 2, "hevc": 3}
_CODEC_RANK_QUALITY = {"vp9": 0, "av1": 1, "avc1": 2, "hevc": 3}


def _video_key(f: Fmt, *, prefer_hdr: bool, compat_profile: bool) -> tuple:  # type: ignore[type-arg]
    """Sort key among streams of the SAME short-side resolution (lower = better)."""
    ranks = _CODEC_RANK_COMPAT if compat_profile else _CODEC_RANK_QUALITY
    hdr_penalty = 0 if f.is_hdr == prefer_hdr else 1
    separate = 0 if not f.has_audio else 1  # video-only DASH streams carry the best quality
    return (
        hdr_penalty,
        -(f.fps or 0),
        separate,
        ranks.get(f.vfamily, 9),
        -(f.vbr or f.tbr or 0),
    )


def _is_original_language(f: Fmt) -> bool:
    note = (f.format_note or "").lower()
    return "original" in note or (f.language_preference or 0) >= 10


def _audio_key(f: Fmt, pref: str) -> tuple:  # type: ignore[type-arg]
    pref_family = "aac" if pref == "aac" else "opus"
    return (
        0 if _is_original_language(f) else 1,
        -(f.language_preference or 0),
        0 if f.afamily == pref_family else (1 if f.afamily in ("aac", "opus") else 2),
        -(f.abr or f.tbr or 0),
    )


def _pick_audio(fmts: list[Fmt], pref: str, mp4_only: bool) -> Fmt | None:
    cands = [f for f in fmts if _usable(f) and f.has_audio and not f.has_video]
    if mp4_only:
        cands = [f for f in cands if audio_mp4_ok(f)]
    return min(cands, key=lambda f: _audio_key(f, pref)) if cands else None


def select_formats(
    fmts: list[Fmt],
    cap: int | None,
    *,
    audio_pref: str = "aac",
    prefer_hdr: bool = False,
    compat_profile: bool = False,
    mkv_fallback: bool = False,
) -> Selection | Unsupported:
    limit = effective_cap(cap)
    videos = _video_candidates(fmts)
    if not videos:
        return Unsupported("No downloadable (non-DRM) video stream is available.", code="no_video_stream")
    within = [f for f in videos if short_side(f) <= limit]
    if not within:
        lowest = min(short_side(f) for f in videos)
        return Unsupported(
            f"The lowest available original resolution ({lowest}p) exceeds the configured limit ({limit}p).",
            code="no_stream_within_limit",
        )
    best_h = max(short_side(f) for f in within)
    reasons = [f"Resolution limit {limit}p (maximum, never upscaled); best original stream within limit: {best_h}p."]
    above = [short_side(f) for f in videos if short_side(f) > limit]
    if above:
        reasons.append(f"Ignored higher source resolutions above the limit (up to {max(above)}p).")

    tier = sorted(
        (f for f in within if short_side(f) == best_h),
        key=lambda f: _video_key(f, prefer_hdr=prefer_hdr, compat_profile=compat_profile),
    )

    # 1) Try an MP4-compatible combination at the best resolution.
    for v in tier:
        if not video_mp4_ok(v):
            continue
        if v.has_audio:  # progressive (combined) stream
            if audio_mp4_ok(v):
                return Selection(v, None, "mp4", limit, best_h, reasons + [f"Video: {v.label()} (combined stream)."])
            continue
        a = _pick_audio(fmts, audio_pref, mp4_only=True)
        if a is None and not any(f.has_audio for f in fmts if _usable(f)):
            # Silent video: archive video only.
            return Selection(v, None, "mp4", limit, best_h, reasons + [f"Video: {v.label()}; no audio track exists."])
        if a is not None:
            note = "" if a.afamily == ("aac" if audio_pref == "aac" else "opus") else " (preferred codec unavailable)"
            return Selection(
                v, a, "mp4", limit, best_h,
                reasons + [f"Video: {v.label()}.", f"Audio: {a.label()}{note}.", "Container: MP4 (stream copy)."],
            )  # fmt: skip

    # 2) No MP4 combination at this resolution — never silently drop resolution.
    if mkv_fallback:
        v = tier[0]
        a = None if v.has_audio else _pick_audio(fmts, audio_pref, mp4_only=False)
        return Selection(
            v, a, "mkv", limit, best_h,
            reasons + [f"Video: {v.label()}.", "No MP4-compatible stream combination at this resolution; "
                       "using the optional MKV fallback (stream copy)."],
        )  # fmt: skip
    codecs = ", ".join(sorted({v.vfamily for v in tier}))
    return Unsupported(
        f"No MP4-compatible stream combination exists at the best available resolution ({best_h}p; codecs: "
        f"{codecs}). DownTube will not reduce the resolution silently. Enable the MKV fallback to archive it.",
        code="no_mp4_combination",
        source_best_height=best_h,
    )
