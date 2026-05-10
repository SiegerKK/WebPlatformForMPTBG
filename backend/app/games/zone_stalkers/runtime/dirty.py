"""Dirty-set helper functions for TickRuntime.

These are the *only* intended write paths for mutation tracking.
Callers that still mutate state directly without these helpers are fine —
the dirty-delta builder falls back to full diff when dirty sets are incomplete.
"""
from __future__ import annotations

from typing import Any

from app.games.zone_stalkers.runtime.tick_runtime import TickRuntime


# ── Mark helpers ─────────────────────────────────────────────────────────────

def mark_agent_dirty(runtime: TickRuntime | None, agent_id: str | None) -> None:
    """Mark agent *agent_id* as modified in this tick."""
    if runtime and agent_id:
        runtime.dirty_agents.add(str(agent_id))


def mark_location_dirty(runtime: TickRuntime | None, location_id: str | None) -> None:
    """Mark location *location_id* as modified in this tick."""
    if runtime and location_id:
        runtime.dirty_locations.add(str(location_id))


def mark_trader_dirty(runtime: TickRuntime | None, trader_id: str | None) -> None:
    """Mark trader *trader_id* as modified in this tick."""
    if runtime and trader_id:
        runtime.dirty_traders.add(str(trader_id))


def mark_state_dirty(runtime: TickRuntime | None, field: str | None) -> None:
    """Mark a top-level state field as modified in this tick."""
    if runtime and field:
        runtime.dirty_state_fields.add(str(field))


# ── Setter helpers ────────────────────────────────────────────────────────────

def set_agent_field(
    state: dict[str, Any],
    runtime: TickRuntime | None,
    agent_id: str,
    key: str,
    value: Any,
) -> bool:
    """Set *key* on agent *agent_id* to *value* only if changed.

    Returns True if the value was changed (and the agent was marked dirty).
    """
    agent = state.get("agents", {}).get(agent_id)
    if not agent:
        return False
    if agent.get(key) == value:
        return False
    agent[key] = value
    mark_agent_dirty(runtime, agent_id)
    return True


def set_location_field(
    state: dict[str, Any],
    runtime: TickRuntime | None,
    location_id: str,
    key: str,
    value: Any,
) -> bool:
    """Set *key* on location *location_id* to *value* only if changed."""
    location = state.get("locations", {}).get(location_id)
    if not location:
        return False
    if location.get(key) == value:
        return False
    location[key] = value
    mark_location_dirty(runtime, location_id)
    return True


def set_trader_field(
    state: dict[str, Any],
    runtime: TickRuntime | None,
    trader_id: str,
    key: str,
    value: Any,
) -> bool:
    """Set *key* on trader *trader_id* to *value* only if changed."""
    trader = state.get("traders", {}).get(trader_id)
    if not trader:
        return False
    if trader.get(key) == value:
        return False
    trader[key] = value
    mark_trader_dirty(runtime, trader_id)
    return True


def set_state_field(
    state: dict[str, Any],
    runtime: TickRuntime | None,
    field: str,
    value: Any,
) -> bool:
    """Set top-level *field* in state to *value* only if changed."""
    if state.get(field) == value:
        return False
    state[field] = value
    mark_state_dirty(runtime, field)
    return True
