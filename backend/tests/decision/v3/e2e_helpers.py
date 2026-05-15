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


def run_until_or_dump(
    state: dict[str, Any],
    predicate: Callable[[dict[str, Any], list[dict[str, Any]]], bool],
    max_ticks: int = 1000,
    *,
    agent_id: str = "hunter",
    label: str = "",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    for _ in range(max_ticks):
        state, events = tick_zone_map(state)
        if predicate(state, events):
            return state, events

    agent = state.get("agents", {}).get(agent_id, {})
    target_id = agent.get("kill_target_id")
    target = state.get("agents", {}).get(target_id, {}) if target_id else {}

    records = ((agent.get("memory_v3") or {}).get("records") or {})
    recent_records = sorted(
        [record for record in records.values() if isinstance(record, dict)],
        key=lambda record: int(record.get("created_turn") or 0),
    )[-20:]

    raise AssertionError(
        {
            "label": label,
            "world_turn": state.get("world_turn"),
            "agent": {
                "location_id": agent.get("location_id"),
                "hp": agent.get("hp"),
                "money": agent.get("money"),
                "global_goal": agent.get("global_goal"),
                "global_goal_achieved": agent.get("global_goal_achieved"),
                "has_left_zone": agent.get("has_left_zone"),
                "active_objective": agent.get("active_objective"),
                "current_goal": agent.get("current_goal"),
                "scheduled_action": agent.get("scheduled_action"),
                "active_plan_v3": agent.get("active_plan_v3"),
                "brain_v3_context": agent.get("brain_v3_context"),
            },
            "target": {
                "target_id": target_id,
                "is_alive": target.get("is_alive"),
                "hp": target.get("hp"),
                "location_id": target.get("location_id"),
            },
            "recent_memory_kinds": [
                {
                    "turn": record.get("created_turn"),
                    "kind": record.get("kind"),
                    "details": record.get("details"),
                }
                for record in recent_records
            ],
            "last_events": events[-20:] if isinstance(events, list) else events,
        }
    )


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
    return any(_matches_action_kind(record, action_kind) for record in _memory_v3_records(agent))


def memories(agent: dict[str, Any], action_kind: str) -> list[dict[str, Any]]:
    return [
        record
        for record in _memory_v3_records(agent)
        if _matches_action_kind(record, action_kind)
    ]


def any_objective_decision(agent: dict[str, Any], objective_key: str) -> bool:
    return any(
        _matches_action_kind(record, "objective_decision")
        and (record.get("details", {}) or {}).get("objective_key") == objective_key
        for record in _memory_v3_records(agent)
    )


def first_objective_turn(agent: dict[str, Any], objective_key: str) -> int | None:
    turns: list[int] = []
    for record in _memory_v3_records(agent):
        details = record.get("details", {}) or {}
        if _matches_action_kind(record, "objective_decision") and details.get("objective_key") == objective_key:
            turn = record.get("created_turn")
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
