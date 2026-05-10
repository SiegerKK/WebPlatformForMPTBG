"""TickProfiler — lightweight CPU-time profiler for Zone Stalkers ticks."""
from __future__ import annotations

from contextlib import contextmanager
from time import perf_counter
from typing import Any


class TickProfiler:
    """Collects per-section wall-clock timings and named counters for one tick."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.sections: dict[str, float] = {}
        self.counters: dict[str, int] = {}

    @contextmanager
    def section(self, name: str):
        """Context-manager that accumulates elapsed ms for *name*."""
        if not self.enabled:
            yield
            return
        started = perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (perf_counter() - started) * 1000.0
            self.sections[name] = self.sections.get(name, 0.0) + elapsed_ms

    def inc(self, name: str, value: int = 1) -> None:
        """Increment counter *name* by *value*."""
        if not self.enabled:
            return
        self.counters[name] = self.counters.get(name, 0) + value

    def set_counter(self, name: str, value: int) -> None:
        """Set counter *name* to *value* (overwrites)."""
        if not self.enabled:
            return
        self.counters[name] = value

    def to_dict(self) -> dict[str, Any]:
        """Return serialisable profiler snapshot."""
        return {
            "sections_ms": {k: round(v, 3) for k, v in self.sections.items()},
            "counters": dict(self.counters),
        }
