from __future__ import annotations

import copy
import json
from typing import Any, Literal

ProjectionMode = Literal["zone-lite", "game", "debug-map", "full"]

INVENTORY_PREVIEW_LIMIT = 20

_GAME_AGENT_STRIP_FIELDS = (
    "memory",
    "memory_v3",
    "brain_trace",
    "active_plan_v3",
    "brain_v3_context",
)


def _json_size_bytes(payload: Any) -> int:
    try:
        return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except Exception:
        return 0


def json_size_bytes(payload: Any) -> int:
    """Public helper: return the JSON-serialized byte size of *payload*."""
    return _json_size_bytes(payload)


def _compact_brain_context(agent: dict[str, Any]) -> dict[str, Any] | None:
    ctx = agent.get("brain_v3_context")
    if not isinstance(ctx, dict):
        return None
    compact: dict[str, Any] = {}
    for key in ("objective_key", "intent_kind", "selected_plan_key"):
        if key in ctx:
            compact[key] = ctx.get(key)
    hunt = ctx.get("hunt_target_belief")
    if isinstance(hunt, dict):
        compact["hunt_target_belief"] = {
            "target_id": hunt.get("target_id"),
            "best_location_id": hunt.get("best_location_id"),
            "best_location_confidence": hunt.get("best_location_confidence"),
            "possible_locations": list(hunt.get("possible_locations", [])[:5]),
            "likely_routes": list(hunt.get("likely_routes", [])[:5]),
            "exhausted_locations": list(hunt.get("exhausted_locations", [])[:10]),
            "lead_count": hunt.get("lead_count"),
        }
    return compact or None


# ── Explicit game projection helpers ─────────────────────────────────────────
# These avoid a full deepcopy of the state dict by building output dicts
# field-by-field.  Only the fields required by the frontend ZoneMapState
# interface are included; heavy data (memory, brain_trace, debug, etc.) is
# never copied.


def _compact_scheduled_action(action: Any) -> dict[str, Any] | None:
    if not isinstance(action, dict):
        return None
    return {
        "type": action.get("type"),
        "turns_remaining": action.get("turns_remaining"),
        "turns_total": action.get("turns_total"),
        "target_id": action.get("target_id"),
        "started_turn": action.get("started_turn"),
    }


def _compact_active_plan(plan: Any) -> dict[str, Any] | None:
    if not isinstance(plan, dict):
        return None
    return {
        "status": plan.get("status"),
        "plan_key": plan.get("plan_key"),
        "current_step": plan.get("current_step"),
        "objective_key": plan.get("objective_key"),
    }


def _compact_equipment(equipment: Any) -> dict[str, Any] | None:
    if not isinstance(equipment, dict):
        return None
    return {
        "weapon": equipment.get("weapon"),
        "armor": equipment.get("armor"),
        "artifact_slots": equipment.get("artifact_slots"),
    }


def _compact_inventory(inventory: Any) -> list[dict[str, Any]]:
    if not isinstance(inventory, list):
        return []
    result = []
    for item in inventory[:INVENTORY_PREVIEW_LIMIT]:
        if isinstance(item, dict):
            result.append({
                "id": item.get("id"),
                "type": item.get("type"),
                "name": item.get("name"),
            })
    return result


def _project_agent_game(agent: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": agent.get("id"),
        "name": agent.get("name"),
        "archetype": agent.get("archetype"),
        "controller": agent.get("controller"),
        "location_id": agent.get("location_id"),
        "is_alive": agent.get("is_alive"),
        "has_left_zone": agent.get("has_left_zone"),
        "hp": agent.get("hp"),
        "max_hp": agent.get("max_hp"),
        "radiation": agent.get("radiation"),
        "hunger": agent.get("hunger"),
        "thirst": agent.get("thirst"),
        "sleepiness": agent.get("sleepiness"),
        "money": agent.get("money"),
        "faction": agent.get("faction"),
        "reputation": agent.get("reputation"),
        "experience": agent.get("experience"),
        "skills": agent.get("skills"),
        "global_goal": agent.get("global_goal"),
        "current_goal": agent.get("current_goal"),
        "risk_tolerance": agent.get("risk_tolerance"),
        "action_used": agent.get("action_used"),
        "scheduled_action": _compact_scheduled_action(agent.get("scheduled_action")),
        "active_plan_summary": _compact_active_plan(agent.get("active_plan_v3")),
        "equipment_summary": _compact_equipment(agent.get("equipment")),
        "inventory_summary": _compact_inventory(agent.get("inventory")),
        # Keep full equipment/inventory for player agent use in UI
        "equipment": agent.get("equipment"),
        "inventory": agent.get("inventory"),
    }


def _project_trader_game(trader: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": trader.get("id"),
        "name": trader.get("name"),
        "archetype": trader.get("archetype"),
        "location_id": trader.get("location_id"),
        "is_alive": trader.get("is_alive"),
        "money": trader.get("money"),
        "inventory": trader.get("inventory"),
        "prices": trader.get("prices"),
    }


def _project_locations_game(locations: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for loc_id, loc in locations.items():
        if not isinstance(loc, dict):
            result[loc_id] = loc
            continue
        result[loc_id] = {
            "id": loc.get("id"),
            "name": loc.get("name"),
            "terrain_type": loc.get("terrain_type"),
            "anomaly_activity": loc.get("anomaly_activity"),
            "dominant_anomaly_type": loc.get("dominant_anomaly_type"),
            "connections": loc.get("connections"),
            "agents": loc.get("agents"),
            "exit_zone": loc.get("exit_zone"),
            "artifacts": loc.get("artifacts"),
            "items": loc.get("items"),
            "region": loc.get("region"),
            "image_url": loc.get("image_url"),
            "debug_layout": loc.get("debug_layout"),
        }
    return result


def _project_zone_game(state: dict[str, Any]) -> dict[str, Any]:
    """Build a game projection without deepcopy — only include frontend-needed fields."""
    agents_raw = state.get("agents", {})
    traders_raw = state.get("traders", {})
    return {
        "context_type": state.get("context_type"),
        "world_turn": state.get("world_turn"),
        "world_day": state.get("world_day"),
        "world_hour": state.get("world_hour"),
        "world_minute": state.get("world_minute"),
        "game_over": state.get("game_over"),
        "emission_active": state.get("emission_active"),
        "emission_scheduled_turn": state.get("emission_scheduled_turn"),
        "emission_ends_turn": state.get("emission_ends_turn"),
        "auto_tick_enabled": state.get("auto_tick_enabled"),
        "auto_tick_speed": state.get("auto_tick_speed"),
        "debug_auto_tick": state.get("debug_auto_tick"),
        "player_agents": state.get("player_agents"),
        "active_events": state.get("active_events"),
        "debug_hunt_traces_enabled": state.get("debug_hunt_traces_enabled"),
        "debug_layout": state.get("debug_layout"),
        "max_turns": state.get("max_turns"),
        "state_revision": state.get("state_revision", 0),
        "map_revision": state.get("map_revision", 0),
        "agents": {id_: _project_agent_game(a) for id_, a in agents_raw.items() if isinstance(a, dict)},
        "traders": {id_: _project_trader_game(t) for id_, t in traders_raw.items() if isinstance(t, dict)},
        "locations": _project_locations_game(state.get("locations", {})),
        # mutants are small dicts, safe to pass through directly
        "mutants": state.get("mutants", {}),
    }


def project_zone_state(*, state: dict[str, Any], mode: ProjectionMode) -> dict[str, Any]:
    if mode == "full":
        return copy.deepcopy(state)

    if mode in {"game", "zone-lite"}:
        return _project_zone_game(state)

    # debug-map: keep deepcopy approach (acceptable for now — called only on demand)
    projected = copy.deepcopy(state)
    agents = projected.get("agents")
    if isinstance(agents, dict):
        for agent in agents.values():
            if not isinstance(agent, dict):
                continue
            agent.pop("memory", None)
            agent.pop("memory_v3", None)
            compact_ctx = _compact_brain_context(agent)
            if compact_ctx is not None:
                agent["brain_v3_context"] = compact_ctx
            else:
                agent.pop("brain_v3_context", None)

    traders = projected.get("traders")
    if isinstance(traders, dict):
        for trader in traders.values():
            if not isinstance(trader, dict):
                continue
            trader.pop("memory", None)
            trader.pop("memory_v3", None)
            trader.pop("brain_trace", None)

    debug = projected.get("debug")
    if isinstance(debug, dict):
        if isinstance(debug.get("hunt_search_by_agent"), dict):
            debug["hunt_search_by_agent"] = {
                key: value for idx, (key, value) in enumerate(debug["hunt_search_by_agent"].items()) if idx < 20
            }
        if isinstance(debug.get("location_hunt_traces"), dict):
            debug["location_hunt_traces"] = {
                key: value for idx, (key, value) in enumerate(debug["location_hunt_traces"].items()) if idx < 60
            }
    return projected


def build_zone_state_size_report(state: dict[str, Any]) -> dict[str, Any]:
    debug = state.get("debug") if isinstance(state.get("debug"), dict) else {}
    return {
        "state_size_bytes": _json_size_bytes(state),
        "zone_lite_size_bytes": _json_size_bytes(project_zone_state(state=state, mode="zone-lite")),
        "game_projection_size_bytes": _json_size_bytes(project_zone_state(state=state, mode="game")),
        "debug_map_projection_size_bytes": _json_size_bytes(project_zone_state(state=state, mode="debug-map")),
        "full_projection_size_bytes": _json_size_bytes(project_zone_state(state=state, mode="full")),
        "debug_hunt_search_bytes": _json_size_bytes(debug.get("hunt_search_by_agent")),
        "location_hunt_traces_bytes": _json_size_bytes(debug.get("location_hunt_traces")),
    }
