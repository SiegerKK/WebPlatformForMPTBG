from __future__ import annotations

import copy
import json
from typing import Any, Literal

ProjectionMode = Literal["zone-lite", "game", "debug-map", "full"]

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


def project_zone_state(*, state: dict[str, Any], mode: ProjectionMode) -> dict[str, Any]:
    if mode == "full":
        return copy.deepcopy(state)

    projected = copy.deepcopy(state)
    agents = projected.get("agents")
    if isinstance(agents, dict):
        for agent in agents.values():
            if not isinstance(agent, dict):
                continue
            if mode in {"game", "zone-lite"}:
                for field in _GAME_AGENT_STRIP_FIELDS:
                    agent.pop(field, None)
            elif mode == "debug-map":
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
            if mode in {"game", "zone-lite", "debug-map"}:
                trader.pop("memory", None)
                trader.pop("memory_v3", None)
                trader.pop("brain_trace", None)

    if mode in {"game", "zone-lite"}:
        projected.pop("debug", None)
    elif mode == "debug-map":
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

