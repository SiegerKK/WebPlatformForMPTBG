"""TickRuntime — per-tick transient state that is NEVER persisted to the DB."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TickRuntime:
    """Transient runtime state for one game tick.

    Holds the profiler and dirty-sets.  Must not be stored in state_blob.
    """

    profiler: Any | None = None
    dirty_agents: set[str] = field(default_factory=set)
    dirty_locations: set[str] = field(default_factory=set)
    dirty_traders: set[str] = field(default_factory=set)
    dirty_state_fields: set[str] = field(default_factory=set)
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_debug_counters(self) -> dict[str, int]:
        """Return dirty-set sizes as a counters dict for the profiler."""
        return {
            "dirty_agents_count": len(self.dirty_agents),
            "dirty_locations_count": len(self.dirty_locations),
            "dirty_traders_count": len(self.dirty_traders),
            "dirty_state_fields_count": len(self.dirty_state_fields),
        }
