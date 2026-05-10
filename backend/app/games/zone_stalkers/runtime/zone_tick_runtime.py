"""ZoneTickRuntime — copy-on-write runtime state for one Zone Stalkers tick."""
from __future__ import annotations

import copy
from typing import Any

from app.games.zone_stalkers.runtime.tick_runtime import TickRuntime


class ZoneTickRuntime(TickRuntime):
    """Per-tick runtime that supports copy-on-write mutation helpers."""

    def __init__(self, source_state: dict[str, Any], profiler: Any | None = None) -> None:
        super().__init__(profiler=profiler)
        self.source_state = source_state
        self.state: dict[str, Any] = dict(source_state)
        self._agents_copied = False
        self._locations_copied = False
        self._traders_copied = False
        self._copied_agents: set[str] = set()
        self._copied_locations: set[str] = set()
        self._copied_traders: set[str] = set()
        self._nested_copies = 0

    @property
    def cow_agents_copied(self) -> int:
        return len(self._copied_agents)

    @property
    def cow_locations_copied(self) -> int:
        return len(self._copied_locations)

    @property
    def cow_traders_copied(self) -> int:
        return len(self._copied_traders)

    @property
    def cow_nested_copies(self) -> int:
        return self._nested_copies

    def _inc_nested_copy(self) -> None:
        self._nested_copies += 1
        if self.profiler is not None:
            try:
                self.profiler.inc("cow_nested_copies")
            except Exception:
                pass

    def ensure_agents_map(self) -> dict[str, Any]:
        if not self._agents_copied:
            self.state["agents"] = dict(self.state.get("agents", {}))
            self._agents_copied = True
        return self.state.get("agents", {})

    def agent(self, agent_id: str) -> dict[str, Any]:
        agents = self.ensure_agents_map()
        if agent_id not in agents:
            raise KeyError(f"Unknown agent {agent_id}")
        if agent_id not in self._copied_agents:
            agents[agent_id] = dict(agents[agent_id])
            self._copied_agents.add(agent_id)
            if self.profiler is not None:
                try:
                    self.profiler.inc("cow_agents_copied")
                except Exception:
                    pass
        return agents[agent_id]

    def ensure_locations_map(self) -> dict[str, Any]:
        if not self._locations_copied:
            self.state["locations"] = dict(self.state.get("locations", {}))
            self._locations_copied = True
        return self.state.get("locations", {})

    def location(self, location_id: str) -> dict[str, Any]:
        locations = self.ensure_locations_map()
        if location_id not in locations:
            raise KeyError(f"Unknown location {location_id}")
        if location_id not in self._copied_locations:
            locations[location_id] = dict(locations[location_id])
            self._copied_locations.add(location_id)
            if self.profiler is not None:
                try:
                    self.profiler.inc("cow_locations_copied")
                except Exception:
                    pass
        return locations[location_id]

    def ensure_traders_map(self) -> dict[str, Any]:
        if not self._traders_copied:
            self.state["traders"] = dict(self.state.get("traders", {}))
            self._traders_copied = True
        return self.state.get("traders", {})

    def trader(self, trader_id: str) -> dict[str, Any]:
        traders = self.ensure_traders_map()
        if trader_id not in traders:
            raise KeyError(f"Unknown trader {trader_id}")
        if trader_id not in self._copied_traders:
            traders[trader_id] = dict(traders[trader_id])
            self._copied_traders.add(trader_id)
            if self.profiler is not None:
                try:
                    self.profiler.inc("cow_traders_copied")
                except Exception:
                    pass
        return traders[trader_id]

    def mark_agent_dirty(self, agent_id: str) -> None:
        self.dirty_agents.add(agent_id)

    def mark_location_dirty(self, location_id: str) -> None:
        self.dirty_locations.add(location_id)

    def mark_trader_dirty(self, trader_id: str) -> None:
        self.dirty_traders.add(trader_id)

    def mark_state_dirty(self, field: str) -> None:
        self.dirty_state_fields.add(field)

    def set_agent_field(self, agent_id: str, key: str, value: Any) -> bool:
        agent = self.agent(agent_id)
        if agent.get(key) == value:
            return False
        agent[key] = value
        self.mark_agent_dirty(agent_id)
        return True

    def set_location_field(self, location_id: str, key: str, value: Any) -> bool:
        location = self.location(location_id)
        if location.get(key) == value:
            return False
        location[key] = value
        self.mark_location_dirty(location_id)
        return True

    def set_trader_field(self, trader_id: str, key: str, value: Any) -> bool:
        trader = self.trader(trader_id)
        if trader.get(key) == value:
            return False
        trader[key] = value
        self.mark_trader_dirty(trader_id)
        return True

    def set_state_field(self, field: str, value: Any) -> bool:
        if self.state.get(field) == value:
            return False
        self.state[field] = value
        self.mark_state_dirty(field)
        return True

    def mutable_agent_list(self, agent_id: str, key: str) -> list[Any]:
        agent = self.agent(agent_id)
        value = list(agent.get(key, []))
        agent[key] = value
        self.mark_agent_dirty(agent_id)
        self._inc_nested_copy()
        return value

    def mutable_agent_dict(self, agent_id: str, key: str) -> dict[str, Any]:
        agent = self.agent(agent_id)
        value = dict(agent.get(key, {}))
        agent[key] = value
        self.mark_agent_dirty(agent_id)
        self._inc_nested_copy()
        return value

    def mutable_location_list(self, location_id: str, key: str) -> list[Any]:
        location = self.location(location_id)
        value = list(location.get(key, []))
        location[key] = value
        self.mark_location_dirty(location_id)
        self._inc_nested_copy()
        return value

    def mutable_location_dict(self, location_id: str, key: str) -> dict[str, Any]:
        location = self.location(location_id)
        value = dict(location.get(key, {}))
        location[key] = value
        self.mark_location_dirty(location_id)
        self._inc_nested_copy()
        return value

    def mutable_trader_list(self, trader_id: str, key: str) -> list[Any]:
        trader = self.trader(trader_id)
        value = list(trader.get(key, []))
        trader[key] = value
        self.mark_trader_dirty(trader_id)
        self._inc_nested_copy()
        return value

    def mutable_trader_dict(self, trader_id: str, key: str) -> dict[str, Any]:
        trader = self.trader(trader_id)
        value = dict(trader.get(key, {}))
        trader[key] = value
        self.mark_trader_dirty(trader_id)
        self._inc_nested_copy()
        return value

    def prepare_for_legacy_mutation(self) -> None:
        """Defensive bridge for legacy direct writes that are not yet runtime-aware."""
        self.state["agents"] = copy.deepcopy(self.state.get("agents", {}))
        self._agents_copied = True
        self._copied_agents = set(self.state.get("agents", {}).keys())
        self.state["locations"] = copy.deepcopy(self.state.get("locations", {}))
        self._locations_copied = True
        self._copied_locations = set(self.state.get("locations", {}).keys())
        self.state["traders"] = copy.deepcopy(self.state.get("traders", {}))
        self._traders_copied = True
        self._copied_traders = set(self.state.get("traders", {}).keys())
        if "debug" in self.state and isinstance(self.state["debug"], dict):
            self.state["debug"] = copy.deepcopy(self.state["debug"])
        if "combat_interactions" in self.state and isinstance(self.state["combat_interactions"], dict):
            self.state["combat_interactions"] = copy.deepcopy(self.state["combat_interactions"])
        if self.profiler is not None:
            try:
                self.profiler.set_counter("cow_agents_copied", len(self._copied_agents))
                self.profiler.set_counter("cow_locations_copied", len(self._copied_locations))
                self.profiler.set_counter("cow_traders_copied", len(self._copied_traders))
            except Exception:
                pass

    def to_debug_counters(self) -> dict[str, int]:
        counters = super().to_debug_counters()
        counters.update(
            {
                "cow_agents_copied": self.cow_agents_copied,
                "cow_locations_copied": self.cow_locations_copied,
                "cow_traders_copied": self.cow_traders_copied,
                "cow_nested_copies": self.cow_nested_copies,
            }
        )
        return counters
