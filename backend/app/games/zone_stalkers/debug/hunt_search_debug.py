from __future__ import annotations

from collections import defaultdict
from typing import Any

_POSITIVE_LEAD_KINDS = {
    "target_seen",
    "target_last_known_location",
    "target_intel",
    "target_moved",
    "target_route_observed",
    "target_wounded",
    "target_combat_noise",
    "target_death_confirmed",
}
_NEGATIVE_LEAD_KINDS = {
    "target_not_found",
    "target_location_exhausted",
    "no_tracks_found",
    "no_witnesses",
    "target_rumor_unreliable",
    "hunt_failed",
}
_ROUTE_KINDS = {"target_moved", "target_route_observed"}
_EVENT_KINDS = {
    "target_seen",
    "target_not_found",
    "target_moved",
    "target_route_observed",
    "target_death_confirmed",
    "hunt_failed",
    "combat_initiated",
    "combat_resolved",
}
_DEFAULT_FRESHNESS_WINDOW = 2000
_MAX_RECORDS_PER_LOCATION = 20
_MAX_ROUTES_PER_LOCATION = 20
_MAX_POSSIBLE_LOCATIONS = 10
_MAX_SOURCE_REFS = 5


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _target_id_from_record(record: dict[str, Any], details: dict[str, Any]) -> str | None:
    for key in ("target_id", "target_agent_id"):
        value = details.get(key)
        if value:
            return str(value)
    entity_ids = record.get("entity_ids")
    if isinstance(entity_ids, list):
        for entity in entity_ids:
            if isinstance(entity, str) and entity:
                return entity
    return None


def _source_agent_id(record: dict[str, Any], details: dict[str, Any]) -> str | None:
    for key in ("source_agent_id", "witness_id", "trader_id"):
        value = details.get(key)
        if value:
            return str(value)
    if record.get("source_agent_id"):
        return str(record.get("source_agent_id"))
    if record.get("agent_id"):
        return str(record.get("agent_id"))
    return None


def _record_freshness(*, created_turn: int, world_turn: int, freshness_window: int) -> float:
    freshness_window = max(1, int(freshness_window))
    age = max(0, int(world_turn) - int(created_turn))
    return _clamp01(1.0 - (age / freshness_window))


def _normalise_refs(refs: list[str]) -> list[str]:
    return list(dict.fromkeys([ref for ref in refs if ref]))[:_MAX_SOURCE_REFS]


def _current_plan_target_location(agent: dict[str, Any]) -> str | None:
    active_plan = agent.get("active_plan_v3")
    if not isinstance(active_plan, dict):
        return None
    steps = active_plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return None
    idx = _coerce_int(active_plan.get("current_step_index"), 0)
    if idx < 0 or idx >= len(steps):
        idx = 0
    step = steps[idx] if isinstance(steps[idx], dict) else {}
    payload = step.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    target_location = payload.get("target_location_id") or payload.get("location_id")
    return str(target_location) if target_location else None


def build_hunt_search_by_agent(
    *,
    state: dict[str, Any],
    world_turn: int,
    freshness_window: int = _DEFAULT_FRESHNESS_WINDOW,
) -> dict[str, Any]:
    agents = state.get("agents", {})
    if not isinstance(agents, dict):
        return {}

    result: dict[str, Any] = {}
    for agent_id, agent in agents.items():
        if not isinstance(agent, dict):
            continue
        brain_ctx = agent.get("brain_v3_context")
        brain_ctx = brain_ctx if isinstance(brain_ctx, dict) else {}
        belief = brain_ctx.get("hunt_target_belief")
        if not isinstance(belief, dict) or not belief.get("target_id"):
            continue
        target_id = str(belief.get("target_id"))
        target = agents.get(target_id, {}) if isinstance(agents.get(target_id), dict) else {}
        possible_locations = []
        for item in belief.get("possible_locations", [])[:_MAX_POSSIBLE_LOCATIONS]:
            if not isinstance(item, dict):
                continue
            possible_locations.append(
                {
                    "location_id": item.get("location_id"),
                    "probability": _coerce_float(item.get("probability"), 0.0),
                    "confidence": _coerce_float(item.get("confidence"), 0.0),
                    "freshness": _coerce_float(item.get("freshness"), 0.0),
                    "reason": item.get("reason"),
                    "source_refs": _normalise_refs(
                        [str(ref) for ref in item.get("source_refs", []) if isinstance(ref, str)]
                    ),
                }
            )
        likely_routes = []
        for item in belief.get("likely_routes", [])[:_MAX_ROUTES_PER_LOCATION]:
            if not isinstance(item, dict):
                continue
            likely_routes.append(
                {
                    "from_location_id": item.get("from_location_id"),
                    "to_location_id": item.get("to_location_id"),
                    "confidence": _coerce_float(item.get("confidence"), 0.0),
                    "freshness": _coerce_float(item.get("freshness"), 0.0),
                    "reason": item.get("reason"),
                    "source_refs": _normalise_refs(
                        [str(ref) for ref in item.get("source_refs", []) if isinstance(ref, str)]
                    ),
                }
            )
        result[str(agent_id)] = {
            "hunter_id": str(agent_id),
            "hunter_name": str(agent.get("name") or agent_id),
            "target_id": target_id,
            "target_name": str(target.get("name") or target_id),
            "best_location_id": belief.get("best_location_id"),
            "best_location_confidence": _coerce_float(belief.get("best_location_confidence"), 0.0),
            "possible_locations": possible_locations,
            "likely_routes": likely_routes,
            "exhausted_locations": [str(loc) for loc in belief.get("exhausted_locations", []) if loc],
            "lead_count": _coerce_int(belief.get("lead_count"), 0),
            "current_objective": brain_ctx.get("objective_key"),
            "current_plan_target_location_id": _current_plan_target_location(agent),
            "freshness_window_turns": freshness_window,
            "world_turn": world_turn,
        }
    return result


def build_location_hunt_traces(
    *,
    state: dict[str, Any],
    world_turn: int,
    freshness_window: int = _DEFAULT_FRESHNESS_WINDOW,
) -> dict[str, Any]:
    agents = state.get("agents", {})
    if not isinstance(agents, dict):
        return {}

    traces: dict[str, dict[str, Any]] = {}

    def _bucket(location_id: str) -> dict[str, Any]:
        if location_id not in traces:
            traces[location_id] = {
                "location_id": location_id,
                "positive_leads": [],
                "negative_leads": [],
                "routes_in": [],
                "routes_out": [],
                "is_exhausted_for": [],
                "combat_hunt_events": [],
            }
        return traces[location_id]

    for hunter_id, agent in agents.items():
        if not isinstance(agent, dict):
            continue
        memory_v3 = agent.get("memory_v3")
        records = memory_v3.get("records", {}) if isinstance(memory_v3, dict) else {}
        if not isinstance(records, dict):
            continue
        for record_id, record in records.items():
            if not isinstance(record, dict):
                continue
            details = record.get("details")
            details = details if isinstance(details, dict) else {}
            kind = str(record.get("kind") or "")
            if not kind:
                continue
            created_turn = _coerce_int(record.get("created_turn"), world_turn)
            freshness = _record_freshness(
                created_turn=created_turn,
                world_turn=world_turn,
                freshness_window=freshness_window,
            )
            if freshness <= 0:
                continue

            target_id = _target_id_from_record(record, details)
            source_agent_id = _source_agent_id(record, details)
            summary = str(record.get("summary") or kind)
            confidence = _coerce_float(record.get("confidence"), 0.0)
            source_ref = f"memory:{record_id}"
            location_id = str(record.get("location_id") or details.get("location_id") or "")
            from_location_id = str(details.get("from_location_id") or details.get("route_from_id") or "")
            to_location_id = str(details.get("to_location_id") or details.get("route_to_id") or "")
            if kind in _ROUTE_KINDS and not to_location_id and location_id:
                to_location_id = location_id
            if kind == "target_moved" and not location_id and to_location_id:
                location_id = to_location_id

            lead_entry = {
                "id": str(record_id),
                "kind": kind,
                "hunter_id": str(hunter_id),
                "target_id": target_id,
                "source_agent_id": source_agent_id,
                "summary": summary,
                "confidence": confidence,
                "freshness": freshness,
                "turn": created_turn,
                "source_ref": source_ref,
                "failed_search_count": _coerce_int(details.get("failed_search_count"), 0),
                "cooldown_until_turn": details.get("cooldown_until_turn"),
            }

            if location_id and kind in _POSITIVE_LEAD_KINDS:
                _bucket(location_id)["positive_leads"].append(lead_entry)
            if location_id and kind in _NEGATIVE_LEAD_KINDS:
                _bucket(location_id)["negative_leads"].append(lead_entry)
                if kind == "target_not_found" and (
                    _coerce_int(details.get("failed_search_count"), 0) >= 3
                    or _coerce_int(details.get("cooldown_until_turn"), 0) > world_turn
                ):
                    _bucket(location_id)["is_exhausted_for"].append(
                        {
                            "hunter_id": str(hunter_id),
                            "target_id": target_id,
                            "source_agent_id": source_agent_id,
                            "failed_search_count": _coerce_int(details.get("failed_search_count"), 0),
                            "cooldown_until_turn": _coerce_int(details.get("cooldown_until_turn"), 0) or None,
                            "source_ref": source_ref,
                            "turn": created_turn,
                            "freshness": freshness,
                        }
                    )
            if kind == "target_location_exhausted" and location_id:
                _bucket(location_id)["is_exhausted_for"].append(
                    {
                        "hunter_id": str(hunter_id),
                        "target_id": target_id,
                        "source_agent_id": source_agent_id,
                        "failed_search_count": _coerce_int(details.get("failed_search_count"), 0),
                        "cooldown_until_turn": _coerce_int(details.get("cooldown_until_turn"), 0) or None,
                        "source_ref": source_ref,
                        "turn": created_turn,
                        "freshness": freshness,
                    }
                )
            if kind in _ROUTE_KINDS and from_location_id and to_location_id:
                _bucket(to_location_id)["routes_in"].append(
                    {
                        "hunter_id": str(hunter_id),
                        "target_id": target_id,
                        "from_location_id": from_location_id,
                        "to_location_id": to_location_id,
                        "source_agent_id": source_agent_id,
                        "confidence": confidence,
                        "freshness": freshness,
                        "reason": kind,
                        "source_ref": source_ref,
                        "turn": created_turn,
                    }
                )
                _bucket(from_location_id)["routes_out"].append(
                    {
                        "hunter_id": str(hunter_id),
                        "target_id": target_id,
                        "from_location_id": from_location_id,
                        "to_location_id": to_location_id,
                        "source_agent_id": source_agent_id,
                        "confidence": confidence,
                        "freshness": freshness,
                        "reason": kind,
                        "source_ref": source_ref,
                        "turn": created_turn,
                    }
                )
            if kind in _EVENT_KINDS and location_id:
                _bucket(location_id)["combat_hunt_events"].append(
                    {
                        "kind": kind,
                        "hunter_id": str(hunter_id),
                        "target_id": target_id,
                        "source_agent_id": source_agent_id,
                        "summary": summary,
                        "confidence": confidence,
                        "freshness": freshness,
                        "turn": created_turn,
                        "source_ref": source_ref,
                    }
                )

    for location_id, payload in traces.items():
        payload["positive_leads"] = sorted(
            payload["positive_leads"],
            key=lambda item: (-(item.get("confidence") or 0.0), -(item.get("freshness") or 0.0), -(item.get("turn") or 0)),
        )[:_MAX_RECORDS_PER_LOCATION]
        payload["negative_leads"] = sorted(
            payload["negative_leads"],
            key=lambda item: (-(item.get("turn") or 0), -(item.get("freshness") or 0.0)),
        )[:_MAX_RECORDS_PER_LOCATION]
        payload["routes_in"] = sorted(
            payload["routes_in"],
            key=lambda item: (-(item.get("confidence") or 0.0), -(item.get("turn") or 0)),
        )[:_MAX_ROUTES_PER_LOCATION]
        payload["routes_out"] = sorted(
            payload["routes_out"],
            key=lambda item: (-(item.get("confidence") or 0.0), -(item.get("turn") or 0)),
        )[:_MAX_ROUTES_PER_LOCATION]
        payload["is_exhausted_for"] = sorted(
            payload["is_exhausted_for"],
            key=lambda item: (-(item.get("cooldown_until_turn") or 0), -(item.get("failed_search_count") or 0)),
        )[:_MAX_RECORDS_PER_LOCATION]
        payload["combat_hunt_events"] = sorted(
            payload["combat_hunt_events"],
            key=lambda item: (-(item.get("turn") or 0), -(item.get("freshness") or 0.0)),
        )[:_MAX_RECORDS_PER_LOCATION]
        payload["lead_count"] = len(payload["positive_leads"]) + len(payload["negative_leads"])
        payload["route_count"] = len(payload["routes_in"]) + len(payload["routes_out"])
        payload["event_count"] = len(payload["combat_hunt_events"])
        payload["world_turn"] = world_turn
        payload["freshness_window_turns"] = freshness_window

    return traces


def build_hunt_debug_payload(
    *,
    state: dict[str, Any],
    world_turn: int,
    freshness_window: int = _DEFAULT_FRESHNESS_WINDOW,
) -> dict[str, Any]:
    return {
        "hunt_search_by_agent": build_hunt_search_by_agent(
            state=state,
            world_turn=world_turn,
            freshness_window=freshness_window,
        ),
        "location_hunt_traces": build_location_hunt_traces(
            state=state,
            world_turn=world_turn,
            freshness_window=freshness_window,
        ),
    }

