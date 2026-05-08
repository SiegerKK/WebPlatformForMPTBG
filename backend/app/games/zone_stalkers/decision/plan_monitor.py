"""Lightweight PR1 monitor for active scheduled_action continuity checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.games.zone_stalkers.rules.tick_constants import (
    CRITICAL_HP_THRESHOLD,
    CRITICAL_HUNGER_THRESHOLD,
    CRITICAL_THIRST_THRESHOLD,
    THIRST_INCREASE_PER_HOUR,
    HUNGER_INCREASE_PER_HOUR,
)


@dataclass(slots=True)
class PlanMonitorResult:
    decision: Literal["continue", "pause", "abort", "adapt"]
    reason: str
    dominant_pressure: str | None = None
    dominant_pressure_value: float | None = None
    interruptible: bool = True
    should_run_decision_pipeline: bool = False
    should_clear_action_queue: bool = False


def is_v3_monitored_bot(agent: dict[str, Any]) -> bool:
    if not agent.get("is_alive", True):
        return False
    if agent.get("has_left_zone"):
        return False
    if agent.get("archetype") != "stalker_agent":
        return False
    if agent.get("controller", {}).get("kind") != "bot":
        return False
    return True


def _projected(value: float, world_minute: int, increase_per_hour: int) -> float:
    if world_minute == 59:
        return min(100.0, value + increase_per_hour)
    return value


def assess_scheduled_action_v3(
    *,
    agent_id: str,
    agent: dict[str, Any],
    scheduled_action: dict[str, Any],
    state: dict[str, Any],
    world_turn: int,
) -> PlanMonitorResult:
    _ = (agent_id, world_turn)

    if not is_v3_monitored_bot(agent):
        return PlanMonitorResult(decision="continue", reason="not_monitored")

    if not scheduled_action:
        return PlanMonitorResult(decision="continue", reason="no_scheduled_action")

    if scheduled_action.get("emergency_flee"):
        return PlanMonitorResult(
            decision="continue",
            reason="emergency_flee_is_uninterruptible",
            interruptible=False,
        )

    world_minute = int(state.get("world_minute", 0))
    hp = float(agent.get("hp", 100))
    thirst = _projected(float(agent.get("thirst", 0)), world_minute, THIRST_INCREASE_PER_HOUR)
    hunger = _projected(float(agent.get("hunger", 0)), world_minute, HUNGER_INCREASE_PER_HOUR)

    if hp <= CRITICAL_HP_THRESHOLD:
        return PlanMonitorResult(
            decision="abort",
            reason="critical_hp",
            dominant_pressure="hp",
            dominant_pressure_value=hp,
            should_run_decision_pipeline=True,
            should_clear_action_queue=True,
        )

    if thirst >= CRITICAL_THIRST_THRESHOLD:
        return PlanMonitorResult(
            decision="abort",
            reason="critical_thirst",
            dominant_pressure="thirst",
            dominant_pressure_value=thirst,
            should_run_decision_pipeline=True,
            should_clear_action_queue=True,
        )

    if hunger >= CRITICAL_HUNGER_THRESHOLD:
        return PlanMonitorResult(
            decision="abort",
            reason="critical_hunger",
            dominant_pressure="hunger",
            dominant_pressure_value=hunger,
            should_run_decision_pipeline=True,
            should_clear_action_queue=True,
        )

    return PlanMonitorResult(
        decision="continue",
        reason="action_still_valid",
        dominant_pressure=None,
        dominant_pressure_value=None,
    )
