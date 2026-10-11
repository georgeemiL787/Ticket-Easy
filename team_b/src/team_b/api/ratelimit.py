"""A per-conversation limit on how fast messages may arrive (a sliding window, in this process)."""

import math
import time
from collections import defaultdict, deque
from collections.abc import Callable


class RateLimiter:
    """Allows `limit` hits per `window_s` seconds for each key. hit() says how long to wait when refused."""

    def __init__(self, limit: int, window_s: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._limit = limit
        self._window = window_s
        self._clock = clock
        self._hits: defaultdict[tuple[str, str], deque[float]] = defaultdict(deque)

    def hit(self, key: tuple[str, str]) -> int | None:
        """Record a message. None when allowed; otherwise the seconds (at least 1) until it would be allowed."""
        now = self._clock()
        hits = self._hits[key]
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            return max(1, math.ceil(self._window - (now - hits[0])))
        hits.append(now)
        return None

    def forget_idle(self) -> None:
        """Drop keys with no recent hits (called now and then so the table does not grow forever)."""
        now = self._clock()
        for key in [k for k, hits in self._hits.items() if not hits or now - hits[-1] >= self._window]:
            del self._hits[key]
