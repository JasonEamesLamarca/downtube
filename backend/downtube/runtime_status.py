"""Liveness flags reported by /api/health. Background components update these."""

from __future__ import annotations

import threading

_lock = threading.Lock()
_state = {"worker": False, "scheduler": False, "archive_writable": False}


def set_flag(name: str, value: bool) -> None:
    with _lock:
        _state[name] = value


def snapshot() -> dict[str, bool]:
    with _lock:
        return dict(_state)
