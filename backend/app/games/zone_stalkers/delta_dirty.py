"""
Dirty-set based delta builder for Zone Stalkers.

Builds a zone_delta using only the agents/locations/traders that were marked
dirty during the tick (via TickRuntime dirty sets).  Falls back to the full
old_state/new_state diff when dirty sets are missing or empty.
"""
from __future__ import annotations

from typing import Any

from app.games.zone_stalkers.delta import (
    compact_agent_for_delta,
    compact_location_for_delta,
    _compact_event_for_delta,
    WS_EVENT_PREVIEW_LIMIT,
)


def should_use_dirty_delta(runtime: Any) -> bool:
    """Return True if *runtime* has non-empty dirty sets (dirty delta is useful)."""
    if runtime is None:
        return False
    return bool(
        getattr(runtime, "dirty_agents", None)
        or getattr(runtime, "dirty_locations", None)
        or getattr(runtime, "dirty_traders", None)
    )


def build_zone_delta_from_dirty(
    *,
    state: dict[str, Any],
    runtime: Any,
    events: list[dict[str, Any]],
    mode: str = "game",
    old_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact delta using dirty sets from *runtime*.

    If runtime dirty sets are empty but world/state changed (detectable via
    *old_state*), falls back to a full diff so nothing is silently dropped.

    Returns the same shape as ``build_zone_delta``.
    """
    dirty_agents: set[str] = set(getattr(runtime, "dirty_agents", set()) or set())
    dirty_locations: set[str] = set(getattr(runtime, "dirty_locations", set()) or set())
    dirty_traders: set[str] = set(getattr(runtime, "dirty_traders", set()) or set())

    agents_map = state.get("agents") or {}
    locations_map = state.get("locations") or {}
    traders_map = state.get("traders") or {}

    # ── Agent delta ──────────────────────────────────────────────────────────
    agent_changes: dict[str, Any] = {}
    for agent_id in dirty_agents:
        agent = agents_map.get(agent_id)
        if isinstance(agent, dict):
            agent_changes[agent_id] = compact_agent_for_delta(agent)

    # ── Location delta ───────────────────────────────────────────────────────
    location_changes: dict[str, Any] = {}
    for loc_id in dirty_locations:
        loc = locations_map.get(loc_id)
        if isinstance(loc, dict):
            location_changes[loc_id] = compact_location_for_delta(loc)

    # ── Trader delta ─────────────────────────────────────────────────────────
    trader_changes: dict[str, Any] = {}
    for trader_id in dirty_traders:
        trader = traders_map.get(trader_id)
        if isinstance(trader, dict):
            trader_changes[trader_id] = {
                "location_id": trader.get("location_id"),
                "is_alive": trader.get("is_alive"),
                "money": trader.get("money"),
                "inventory": trader.get("inventory"),
                "prices": trader.get("prices"),
            }

    # ── State-level changes (world time + hot fields) ────────────────────────
    # Always include current world time so the frontend stays in sync.
    state_changes: dict[str, Any] = {}
    dirty_state_fields: set[str] = set(getattr(runtime, "dirty_state_fields", set()) or set())
    for field in dirty_state_fields:
        state_changes[field] = state.get(field)

    # ── Fallback: if dirty sets are all empty but old_state differs, use full diff ──
    if (
        not agent_changes
        and not location_changes
        and not trader_changes
        and old_state is not None
    ):
        from app.games.zone_stalkers.delta import build_zone_delta
        return build_zone_delta(
            old_state=old_state,
            new_state=state,
            events=events,
            mode=mode,  # type: ignore[arg-type]
        )

    # ── Event preview ────────────────────────────────────────────────────────
    preview = [_compact_event_for_delta(e) for e in events[:WS_EVENT_PREVIEW_LIMIT]]

    return {
        "base_revision": state.get("state_revision", 0),
        "revision": state.get("state_revision", 0),
        "world": {
            "world_turn": state.get("world_turn"),
            "world_day": state.get("world_day"),
            "world_hour": state.get("world_hour"),
            "world_minute": state.get("world_minute"),
        },
        "changes": {
            "agents": agent_changes,
            "locations": location_changes,
            "traders": trader_changes,
            "state": state_changes,
        },
        "events": {
            "count": len(events),
            "preview": preview,
        },
    }
