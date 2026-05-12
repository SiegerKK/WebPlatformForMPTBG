from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.games.zone_stalkers.rules.tick_rules import tick_zone_map


def run_until(
    state: dict[str, Any],
    predicate: Callable[[dict[str, Any], list[dict[str, Any]]], bool],
    max_ticks: int = 1000,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    for _ in range(max_ticks):
        state, events = tick_zone_map(state)
        if predicate(state, events):
            return state, events
    raise AssertionError("condition not reached")


def _memory_v3_records(agent: dict[str, Any]) -> list[dict[str, Any]]:
    records = ((agent.get("memory_v3") or {}).get("records") or {})
    if not isinstance(records, dict):
        return []
    return [record for record in records.values() if isinstance(record, dict)]


def _matches_action_kind(record: dict[str, Any], action_kind: str) -> bool:
    details = record.get("details", {})
    return (
        str(record.get("kind", "")) == action_kind
        or details.get("action_kind") == action_kind
    )


def any_memory(agent: dict[str, Any], action_kind: str) -> bool:
    return any(_matches_action_kind(record, action_kind) for record in _memory_v3_records(agent)) or any(
        mem.get("effects", {}).get("action_kind") == action_kind
        for mem in agent.get("memory", [])
        if isinstance(mem, dict)
    )


def memories(agent: dict[str, Any], action_kind: str) -> list[dict[str, Any]]:
    v3_records = [
        record
        for record in _memory_v3_records(agent)
        if _matches_action_kind(record, action_kind)
    ]
    legacy_records = [
        mem
        for mem in agent.get("memory", [])
        if isinstance(mem, dict) and mem.get("effects", {}).get("action_kind") == action_kind
    ]
    return v3_records + legacy_records


def any_objective_decision(agent: dict[str, Any], objective_key: str) -> bool:
    v3_found = any(
        _matches_action_kind(record, "objective_decision")
        and (record.get("details", {}) or {}).get("objective_key") == objective_key
        for record in _memory_v3_records(agent)
    )
    if v3_found:
        return True
    return any(
        mem.get("effects", {}).get("action_kind") == "objective_decision"
        and mem.get("effects", {}).get("objective_key") == objective_key
        for mem in agent.get("memory", [])
        if isinstance(mem, dict)
    )


def first_objective_turn(agent: dict[str, Any], objective_key: str) -> int | None:
    turns: list[int] = []
    for record in _memory_v3_records(agent):
        details = record.get("details", {}) or {}
        if _matches_action_kind(record, "objective_decision") and details.get("objective_key") == objective_key:
            turn = record.get("created_turn")
            if isinstance(turn, int):
                turns.append(turn)
    for mem in agent.get("memory", []):
        if not isinstance(mem, dict):
            continue
        effects = mem.get("effects", {})
        if effects.get("action_kind") == "objective_decision" and effects.get("objective_key") == objective_key:
            turn = mem.get("world_turn")
            if isinstance(turn, int):
                turns.append(turn)
    return min(turns) if turns else None


def first_memory_turn(agent: dict[str, Any], action_kind: str, **effect_filters: Any) -> int | None:
    turns: list[int] = []
    for record in _memory_v3_records(agent):
        details = record.get("details", {}) or {}
        if not _matches_action_kind(record, action_kind):
            continue
        if any(details.get(key) != value for key, value in effect_filters.items()):
            continue
        turn = record.get("created_turn")
        if isinstance(turn, int):
            turns.append(turn)
    for mem in agent.get("memory", []):
        if not isinstance(mem, dict):
            continue
        effects = mem.get("effects", {})
        if effects.get("action_kind") != action_kind:
            continue
        if any(effects.get(key) != value for key, value in effect_filters.items()):
            continue
        turn = mem.get("world_turn")
        if isinstance(turn, int):
            turns.append(turn)
    return min(turns) if turns else None


def any_active_plan_event(agent: dict[str, Any], event_kind: str) -> bool:
    events = (agent.get("brain_trace", {}) or {}).get("events", [])
    return any(
        isinstance(event, dict)
        and event.get("mode") == "active_plan"
        and event.get("decision") == event_kind
        for event in events
    )


def any_active_plan_step(agent: dict[str, Any], step_kind: str) -> bool:
    events = (agent.get("brain_trace", {}) or {}).get("events", [])
    return any(
        isinstance(event, dict)
        and event.get("mode") == "active_plan"
        and event.get("decision") == "active_plan_step_completed"
        and event.get("active_plan", {}).get("completed_step_kind") == step_kind
        for event in events
    )
