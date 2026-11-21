"""Tiny in-memory sliding-window rate limiter (single process, so no shared store needed)."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import Request

from downtube.main import ApiError


class RateLimiter:
    def __init__(self, limit: int, window_s: float = 60.0) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window_s:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self._hits) > 10_000:  # bound memory
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


auth_limiter = RateLimiter(limit=20, window_s=60.0)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def rate_limit_auth(request: Request) -> None:
    if not auth_limiter.check(client_ip(request)):
        raise ApiError(429, "rate_limited", "too many authentication requests, slow down")
