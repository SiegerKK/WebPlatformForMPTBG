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


def any_memory(agent: dict[str, Any], action_kind: str) -> bool:
    return any(
        mem.get("effects", {}).get("action_kind") == action_kind
        for mem in agent.get("memory", [])
        if isinstance(mem, dict)
    )


def memories(agent: dict[str, Any], action_kind: str) -> list[dict[str, Any]]:
    return [
        mem
        for mem in agent.get("memory", [])
        if isinstance(mem, dict) and mem.get("effects", {}).get("action_kind") == action_kind
    ]


def any_objective_decision(agent: dict[str, Any], objective_key: str) -> bool:
    return any(
        mem.get("effects", {}).get("action_kind") == "objective_decision"
        and mem.get("effects", {}).get("objective_key") == objective_key
        for mem in agent.get("memory", [])
        if isinstance(mem, dict)
    )
