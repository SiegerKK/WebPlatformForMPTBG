from __future__ import annotations

import math
from typing import Any

from app.games.zone_stalkers.rules.tick_constants import (
    CRITICAL_HUNGER_THRESHOLD,
    CRITICAL_THIRST_THRESHOLD,
    HUNGER_INCREASE_PER_HOUR,
    SLEEPINESS_INCREASE_PER_HOUR,
    THIRST_INCREASE_PER_HOUR,
)
from app.games.zone_stalkers.runtime.scheduler import schedule_task

_NEED_RATES_PER_TURN = {
    "hunger": HUNGER_INCREASE_PER_HOUR / 60.0,
    "thirst": THIRST_INCREASE_PER_HOUR / 60.0,
    "sleepiness": SLEEPINESS_INCREASE_PER_HOUR / 60.0,
}
_SOFT_THRESHOLD = 70.0
_CRITICAL_SLEEPINESS_THRESHOLD = 90.0  # hunger/thirst use tick_constants values


def _clamp_need(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def ensure_needs_state(agent: dict[str, Any], world_turn: int) -> bool:
    if isinstance(agent.get("needs_state"), dict):
        return False
    agent["needs_state"] = {
        "hunger": {"base": float(agent.get("hunger", 0.0)), "updated_turn": int(world_turn)},
        "thirst": {"base": float(agent.get("thirst", 0.0)), "updated_turn": int(world_turn)},
        "sleepiness": {"base": float(agent.get("sleepiness", 0.0)), "updated_turn": int(world_turn)},
        "revision": 1,
    }
    return True


def get_need(agent: dict[str, Any], need_key: str, world_turn: int) -> float:
    ensure_needs_state(agent, world_turn)
    needs_state = agent.get("needs_state", {})
    node = needs_state.get(need_key, {})
    base = float(node.get("base", agent.get(need_key, 0.0)))
    updated_turn = int(node.get("updated_turn", world_turn))
    elapsed = max(0, int(world_turn) - updated_turn)
    rate = float(_NEED_RATES_PER_TURN.get(need_key, 0.0))
    return _clamp_need(base + elapsed * rate)


def materialize_needs(agent: dict[str, Any], world_turn: int) -> dict[str, float]:
    values = {
        "hunger": get_need(agent, "hunger", world_turn),
        "thirst": get_need(agent, "thirst", world_turn),
        "sleepiness": get_need(agent, "sleepiness", world_turn),
    }
    for key, value in values.items():
        agent[key] = value
    return values


def set_need(agent: dict[str, Any], need_key: str, value: float, world_turn: int) -> None:
    set_needs(agent, {need_key: value}, world_turn)


def set_needs(agent: dict[str, Any], updates: dict[str, float], world_turn: int) -> None:
    ensure_needs_state(agent, world_turn)
    needs_state = agent["needs_state"]
    changed = False
    for need_key, value in updates.items():
        if need_key not in _NEED_RATES_PER_TURN:
            continue
        node = needs_state.setdefault(need_key, {})
        node["base"] = _clamp_need(value)
        node["updated_turn"] = int(world_turn)
        agent[need_key] = node["base"]
        changed = True
    if changed:
        needs_state["revision"] = int(needs_state.get("revision", 0)) + 1


def schedule_need_thresholds(
    state: dict[str, Any],
    runtime: Any,
    agent_id: str,
    agent: dict[str, Any],
    world_turn: int,
) -> None:
    ensure_needs_state(agent, world_turn)
    needs_state = agent["needs_state"]
    revision = int(needs_state.get("revision", 0))
    threshold_tasks = needs_state.setdefault("_threshold_tasks", {})
    for need_key, rate in _NEED_RATES_PER_TURN.items():
        if rate <= 0:
            continue
        current = get_need(agent, need_key, world_turn)
        for threshold_name, threshold_value in (
            ("soft", _SOFT_THRESHOLD),
            ("critical", _critical_threshold_for_need(need_key)),
        ):
            dedupe_key = f"{need_key}:{threshold_name}"
            if threshold_tasks.get(dedupe_key) == revision:
                continue
            if current >= threshold_value:
                continue
            turns_until = math.ceil((threshold_value - current) / rate)
            schedule_task(
                state,
                runtime,
                int(world_turn) + max(1, turns_until),
                {
                    "kind": "need_threshold_crossed",
                    "agent_id": agent_id,
                    "need": need_key,
                    "threshold": threshold_name,
                    "needs_revision": revision,
                },
            )
            threshold_tasks[dedupe_key] = revision


def _critical_threshold_for_need(need_key: str) -> float:
    if need_key == "hunger":
        return float(CRITICAL_HUNGER_THRESHOLD)
    if need_key == "thirst":
        return float(CRITICAL_THIRST_THRESHOLD)
    return float(_CRITICAL_SLEEPINESS_THRESHOLD)


def project_needs(agent: dict[str, Any], world_turn: int) -> dict[str, float]:
    """
    Compute current hunger/thirst/sleepiness without mutating the agent.

    Same as materialize_needs but does NOT write back to the agent dict.
    """
    # get_need calls ensure_needs_state which mutates if no needs_state exists,
    # but we should NOT call ensure_needs_state here since we don't want to mutate.
    needs_state = agent.get("needs_state")
    if not isinstance(needs_state, dict):
        # Fallback: use raw fields without creating needs_state
        return {
            "hunger": float(agent.get("hunger", 0.0)),
            "thirst": float(agent.get("thirst", 0.0)),
            "sleepiness": float(agent.get("sleepiness", 0.0)),
        }
    result: dict[str, float] = {}
    for need_key in ("hunger", "thirst", "sleepiness"):
        node = needs_state.get(need_key, {})
        if not isinstance(node, dict):
            result[need_key] = float(agent.get(need_key, 0.0))
            continue
        base = float(node.get("base", agent.get(need_key, 0.0)))
        updated_turn = int(node.get("updated_turn", world_turn))
        elapsed = max(0, int(world_turn) - updated_turn)
        rate = float(_NEED_RATES_PER_TURN.get(need_key, 0.0))
        result[need_key] = _clamp_need(base + elapsed * rate)
    return result
