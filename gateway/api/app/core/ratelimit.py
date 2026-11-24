"""Per-client sliding-window rate limit (SPEC: 600 requests per minute per client)."""

from __future__ import annotations

import math
import threading
from collections import deque


class RateLimiter:
    def __init__(self, per_minute: int, window_seconds: float = 60.0) -> None:
        self.per_minute: int = per_minute
        self.window: float = window_seconds
        self._lock: threading.Lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str, now: float) -> int | None:
        """Record a hit; return ``None`` if allowed or the Retry-After seconds if not."""
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            cutoff = now - self.window
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return max(1, math.ceil(hits[0] + self.window - now))
            hits.append(now)
            return None
