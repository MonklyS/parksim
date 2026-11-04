"""A minimal discrete-event scheduler.

Instead of updating every guest every second, the simulation schedules *events*
("guest arrives at the coaster at 10:42:17", "train dispatches at 10:43:00") and jumps
straight from one event to the next. This is what lets a full park day with thousands
of guests run in a few seconds of pure Python.
"""

from __future__ import annotations

import heapq
import itertools
from collections.abc import Callable
from typing import Any

_Event = tuple[float, int, Callable[..., None], tuple[Any, ...]]


class Scheduler:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start
        self.processed = 0
        self._heap: list[_Event] = []
        self._seq = itertools.count()  # tie-breaker keeps same-time events in insertion order

    def at(self, time: float, fn: Callable[..., None], *args: Any) -> None:
        """Run ``fn(*args)`` at an absolute time (never in the past)."""
        heapq.heappush(self._heap, (max(time, self.now), next(self._seq), fn, args))

    def after(self, delay: float, fn: Callable[..., None], *args: Any) -> None:
        """Run ``fn(*args)`` after a delay in seconds."""
        self.at(self.now + delay, fn, *args)

    def run_until(self, end: float) -> None:
        """Process every event scheduled at or before ``end``."""
        heap = self._heap
        while heap and heap[0][0] <= end:
            time, _, fn, args = heapq.heappop(heap)
            self.now = time
            fn(*args)
            self.processed += 1
        self.now = max(self.now, end)

    def __len__(self) -> int:
        return len(self._heap)
