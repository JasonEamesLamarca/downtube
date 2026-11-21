"""Path safety, deterministic naming and disk-space helpers.

Every filesystem path that originates from user input, settings, or YouTube metadata
must pass through this module before being touched.
"""

from __future__ import annotations

import os
import re
import shutil
import unicodedata
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from downtube.config import get_config
from downtube.models import Channel, Video
from downtube.schemas import BrowseResult, DirEntry


class StorageError(Exception):
    pass


_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_WS = re.compile(r"\s+")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def allowed_mounts() -> list[Path]:
    return [Path(os.path.realpath(p)) for p in get_config().storage_mounts]


def resolve_within_mounts(p: str | Path, mounts: list[Path] | None = None) -> Path:
    """Resolve ``p`` (following symlinks) and require it to stay inside an allowed mount.

    Raises StorageError for relative paths, traversal, or symlinks escaping the mounts.
    """
    raw = str(p)
    if not raw or "\x00" in raw:
        raise StorageError("invalid path")
    path = Path(raw)
    if not path.is_absolute():
        raise StorageError("path must be absolute")
    real = Path(os.path.realpath(path))
    for m in mounts if mounts is not None else allowed_mounts():
        if real == m or real.is_relative_to(m):
            return real
    raise StorageError("path is outside the allowed storage mounts")


def safe_join(root: Path, *parts: str) -> Path:
    """Join sanitized components under ``root`` and verify the result cannot escape it."""
    root_real = Path(os.path.realpath(root))
    target = root_real.joinpath(*parts)
    real = Path(os.path.realpath(target))
    if not (real == root_real or real.is_relative_to(root_real)):
        raise StorageError("path escapes archive root")
    return target


def _truncate_utf8(s: str, max_bytes: int) -> str:
    b = s.encode("utf-8")
    if len(b) <= max_bytes:
        return s
    return b[:max_bytes].decode("utf-8", errors="ignore").rstrip()


def sanitize_component(name: str | None, max_bytes: int = 180, fallback: str = "untitled") -> str:
    """Make a single, portable path component (Unicode kept, NFC normalised)."""
    s = unicodedata.normalize("NFC", name or "")
    s = _FORBIDDEN.sub(" ", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co"))
    s = _WS.sub(" ", s).strip()
    s = s.lstrip(".").rstrip(". ")
    s = _truncate_utf8(s, max_bytes).rstrip(". ")
    if not s or s.upper().split(".")[0] in _RESERVED:
        s = (s + " " + fallback).strip() if s else fallback
    return s


def channel_folder_name(s: Session, channel: Channel) -> str:
    """Return the stable folder for a channel, assigning it once (rename-safe)."""
    if channel.folder_name:
        return channel.folder_name
    base = sanitize_component(channel.title, max_bytes=120, fallback=channel.id)
    name = base
    clash = s.scalar(select(Channel.id).where(Channel.folder_name == name, Channel.id != channel.id))
    if clash:
        name = f"{base} [{channel.id}]"
    channel.folder_name = name
    s.flush()
    return name


def video_basename(video: Video) -> str:
    date = video.published_at.strftime("%Y-%m-%d") if video.published_at else "0000-00-00"
    title = sanitize_component(video.title, max_bytes=150, fallback=video.id)
    return f"{date} - {title} [{video.id}]"


def video_filename(video: Video, ext: str) -> str:
    return f"{video_basename(video)}.{ext.lstrip('.')}"


def disk_usage(path: Path) -> tuple[int, int]:
    u = shutil.disk_usage(path)
    return u.total, u.free


def has_space_for(root: Path, needed_bytes: int, min_free_bytes: int) -> bool:
    _, free = disk_usage(root)
    return free - max(needed_bytes, 0) >= min_free_bytes


def is_writable(path: Path) -> bool:
    return path.is_dir() and os.access(path, os.W_OK | os.X_OK)


def browse(path: str | None) -> BrowseResult:
    mounts = allowed_mounts()
    if not mounts:
        raise StorageError("no storage mounts configured")
    target = resolve_within_mounts(path, mounts) if path else mounts[0]
    if not target.is_dir():
        raise StorageError("not a directory")
    parent = None if target in mounts else str(target.parent)
    entries = []
    try:
        for child in sorted(target.iterdir(), key=lambda c: c.name.lower()):
            if child.name.startswith(".") or not child.is_dir():
                continue
            try:
                real = resolve_within_mounts(child, mounts)  # skip symlinks escaping mounts
            except StorageError:
                continue
            entries.append(DirEntry(name=child.name, path=str(real), writable=is_writable(real)))
    except PermissionError as e:
        raise StorageError("permission denied") from e
    return BrowseResult(path=str(target), parent=parent, mounts=[str(m) for m in mounts], entries=entries[:1000])
