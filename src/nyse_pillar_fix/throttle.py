"""Client-side pacing to stay under the gateway's inbound read rate (section 5.2).

The gateway reads at most 500 messages (session + application) per rolling 100 ms per
session; excess messages are queued, not rejected, and responses report
FlowIndicator (20005) = 1. Pacing locally keeps latency predictable.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque


class RollingWindowThrottle:
    def __init__(self, max_messages: int = 500, window_seconds: float = 0.1) -> None:
        if max_messages < 1 or window_seconds <= 0:
            raise ValueError("max_messages must be >= 1 and window_seconds > 0")
        self.max_messages = max_messages
        self.window = window_seconds
        self._stamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    def _prune(self, now: float) -> None:
        while self._stamps and now - self._stamps[0] >= self.window:
            self._stamps.popleft()

    def try_acquire(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        self._prune(now)
        if len(self._stamps) < self.max_messages:
            self._stamps.append(now)
            return True
        return False

    def delay(self, now: float | None = None) -> float:
        """Seconds until a slot frees up (0 if one is available)."""
        now = time.monotonic() if now is None else now
        self._prune(now)
        if len(self._stamps) < self.max_messages:
            return 0.0
        return self.window - (now - self._stamps[0])

    async def acquire(self) -> None:
        async with self._lock:
            while not self.try_acquire():
                await asyncio.sleep(max(self.delay(), 0.0005))
