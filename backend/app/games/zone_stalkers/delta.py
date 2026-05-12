"""
Zone Stalkers WebSocket delta builder.

Computes compact domain-specific deltas between two game states.
Only changed agents/locations/traders are included; heavy fields
(memory, brain_trace, debug, etc.) are never included.
"""
from __future__ import annotations

from typing import Any, Iterable, Literal

from app.games.zone_stalkers.projections import INVENTORY_PREVIEW_LIMIT

# Fields compared per agent to detect changes
_AGENT_HOT_FIELDS: frozenset[str] = frozenset({
    "location_id", "is_alive", "has_left_zone",
    "hp", "hunger", "thirst", "sleepiness",
    "money", "current_goal", "global_goal",
    "action_used", "scheduled_action", "active_plan_v3",
    "equipment", "inventory",
})

# Fields to include in agent delta patch
_AGENT_DELTA_FIELDS: frozenset[str] = frozenset({
    "location_id", "is_alive", "has_left_zone",
    "hp", "hunger", "thirst", "sleepiness",
    "money", "current_goal", "global_goal", "action_used",
})

# Fields compared per location to detect changes
_LOCATION_HOT_FIELDS: frozenset[str] = frozenset({
    "agents", "artifacts", "items", "anomaly_activity",
    "dominant_anomaly_type",
    "image_url", "image_slots", "primary_image_slot",
})

# Fields compared per trader to detect changes
_TRADER_HOT_FIELDS: frozenset[str] = frozenset({"location_id", "is_alive", "money", "inventory"})

# State-level fields that are part of the hot delta
_STATE_HOT_FIELDS: frozenset[str] = frozenset({
    "game_over", "emission_active", "emission_scheduled_turn", "emission_ends_turn",
    "active_events", "auto_tick_enabled", "auto_tick_speed",
})

WS_EVENT_PREVIEW_LIMIT = 10


def _compact_scheduled_action_delta(action: Any) -> dict[str, Any] | None:
    if not isinstance(action, dict):
        return None
    return {
        "type": action.get("type"),
        "turns_remaining": action.get("turns_remaining"),
        "turns_total": action.get("turns_total"),
        "target_id": action.get("target_id"),
        "started_turn": action.get("started_turn"),
        "ends_turn": action.get("ends_turn"),
        "revision": action.get("revision"),
        "interruptible": action.get("interruptible"),
    }


def _compact_active_plan_delta(plan: Any) -> dict[str, Any] | None:
    if not isinstance(plan, dict):
        return None
    # Get current step info
    steps = plan.get("steps", [])
    current_step_idx = plan.get("current_step_index", 0)
    current_step = steps[current_step_idx] if steps and 0 <= current_step_idx < len(steps) else None
    return {
        "objective_key": plan.get("objective_key"),
        "status": plan.get("status"),
        "plan_key": plan.get("plan_key"),
        "current_step_kind": current_step.get("kind") if isinstance(current_step, dict) else None,
        "current_step_index": current_step_idx,
        "steps_count": len(steps),
    }


def _compact_equipment_delta(equipment: Any) -> dict[str, Any] | None:
    if not isinstance(equipment, dict):
        return None
    return {
        "weapon": equipment.get("weapon"),
        "armor": equipment.get("armor"),
        "artifact_slots": equipment.get("artifact_slots"),
    }


def _compact_inventory_delta(inventory: Any) -> list[dict[str, Any]]:
    if not isinstance(inventory, list):
        return []
    result = []
    for item in inventory[:INVENTORY_PREVIEW_LIMIT]:
        if isinstance(item, dict):
            result.append({"id": item.get("id"), "type": item.get("type"), "name": item.get("name")})
    return result


def compact_agent_for_delta(agent: dict[str, Any], world_turn: int | None = None) -> dict[str, Any]:
    """Build a compact agent dict containing only hot fields for delta comparison."""
    patch: dict[str, Any] = {f: agent.get(f) for f in _AGENT_DELTA_FIELDS}
    # Overlay derived needs if lazy needs are active
    if world_turn is not None and isinstance(agent.get("needs_state"), dict):
        from app.games.zone_stalkers.needs.lazy_needs import project_needs as _pn  # noqa: PLC0415
        _derived = _pn(agent, world_turn)
        patch["hunger"] = _derived["hunger"]
        patch["thirst"] = _derived["thirst"]
        patch["sleepiness"] = _derived["sleepiness"]
    patch["scheduled_action"] = _compact_scheduled_action_delta(agent.get("scheduled_action"))
    patch["active_plan_summary"] = _compact_active_plan_delta(agent.get("active_plan_v3"))
    # Include equipment/inventory summary if present
    equip = agent.get("equipment")
    if equip is not None:
        patch["equipment_summary"] = _compact_equipment_delta(equip)
    inv = agent.get("inventory")
    if inv is not None:
        patch["inventory_summary"] = _compact_inventory_delta(inv)
    return patch


def compact_location_for_delta(location: dict[str, Any]) -> dict[str, Any]:
    """Build a compact location dict for delta."""
    artifacts = location.get("artifacts") or []
    items = location.get("items") or []
    return {
        "agents": location.get("agents", []),
        "artifact_count": len(artifacts),
        "item_count": len(items),
        "artifacts": artifacts[:5] if artifacts else [],  # compact list if small
        "items": items[:5] if items else [],
        "anomaly_activity": location.get("anomaly_activity"),
        "dominant_anomaly_type": location.get("dominant_anomaly_type"),
        "image_url": location.get("image_url"),
        "image_slots": location.get("image_slots"),
        "primary_image_slot": location.get("primary_image_slot"),
    }


def diff_dict(old: dict[str, Any], new: dict[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    """Return fields from `new` that differ from `old` (by the given keys)."""
    result = {}
    for k in keys:
        new_val = new.get(k)
        old_val = old.get(k)
        if new_val != old_val:
            result[k] = new_val
    return result


def _compact_event_for_delta(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload", {}) if isinstance(event.get("payload"), dict) else {}
    return {
        "event_type": event.get("event_type"),
        "agent_id": payload.get("agent_id"),
        "location_id": payload.get("location_id"),
        "summary": payload.get("summary"),
        "action_kind": payload.get("action_kind"),
    }


def build_zone_delta(
    *,
    old_state: dict[str, Any],
    new_state: dict[str, Any],
    events: list[dict[str, Any]],
    mode: Literal["game", "debug-map"] = "game",
) -> dict[str, Any]:
    """
    Build a compact domain-specific delta between old_state and new_state.

    Returns:
        dict with keys: base_revision, revision, world, changes, events
    """
    # --- Agent changes ---
    old_agents = old_state.get("agents", {}) or {}
    new_agents = new_state.get("agents", {}) or {}
    agent_changes: dict[str, Any] = {}

    all_agent_ids = set(old_agents) | set(new_agents)
    for agent_id in all_agent_ids:
        old_agent = old_agents.get(agent_id, {})
        new_agent = new_agents.get(agent_id, {})
        if not isinstance(old_agent, dict):
            old_agent = {}
        if not isinstance(new_agent, dict):
            continue  # agent removed — skip for MVP
        # Check if any hot field changed.
        # NOTE (PR3 lazy-needs minimal mode):
        # derived hunger/thirst/sleepiness are overlaid only after an agent is already
        # selected for delta by a hot-field change. Pure time-based lazy growth alone
        # does not emit deltas every tick (intentional CPU-friendly trade-off).
        changed = False
        for f in _AGENT_HOT_FIELDS:
            if old_agent.get(f) != new_agent.get(f):
                changed = True
                break
        if changed:
            agent_changes[agent_id] = compact_agent_for_delta(new_agent, world_turn=new_state.get("world_turn"))

    # --- Location changes ---
    old_locs = old_state.get("locations", {}) or {}
    new_locs = new_state.get("locations", {}) or {}
    location_changes: dict[str, Any] = {}

    all_loc_ids = set(old_locs) | set(new_locs)
    for loc_id in all_loc_ids:
        old_loc = old_locs.get(loc_id, {})
        new_loc = new_locs.get(loc_id, {})
        if not isinstance(old_loc, dict):
            old_loc = {}
        if not isinstance(new_loc, dict):
            continue
        changed = False
        for f in _LOCATION_HOT_FIELDS:
            if old_loc.get(f) != new_loc.get(f):
                changed = True
                break
        if changed:
            location_changes[loc_id] = compact_location_for_delta(new_loc)

    # --- Trader changes ---
    old_traders = old_state.get("traders", {}) or {}
    new_traders = new_state.get("traders", {}) or {}
    trader_changes: dict[str, Any] = {}

    for trader_id in set(old_traders) | set(new_traders):
        old_tr = old_traders.get(trader_id, {})
        new_tr = new_traders.get(trader_id, {})
        if not isinstance(old_tr, dict):
            old_tr = {}
        if not isinstance(new_tr, dict):
            continue
        changed = any(old_tr.get(f) != new_tr.get(f) for f in _TRADER_HOT_FIELDS)
        if changed:
            trader_changes[trader_id] = {
                "location_id": new_tr.get("location_id"),
                "is_alive": new_tr.get("is_alive"),
                "money": new_tr.get("money"),
                "inventory": new_tr.get("inventory"),
                "prices": new_tr.get("prices"),
            }

    # --- State-level changes ---
    state_changes: dict[str, Any] = {}
    for f in _STATE_HOT_FIELDS:
        if old_state.get(f) != new_state.get(f):
            state_changes[f] = new_state.get(f)

    # --- Event preview ---
    preview = [_compact_event_for_delta(e) for e in events[:WS_EVENT_PREVIEW_LIMIT]]

    return {
        "base_revision": old_state.get("state_revision", 0),
        "revision": new_state.get("state_revision", 0),
        "world": {
            "world_turn": new_state.get("world_turn"),
            "world_day": new_state.get("world_day"),
            "world_hour": new_state.get("world_hour"),
            "world_minute": new_state.get("world_minute"),
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
