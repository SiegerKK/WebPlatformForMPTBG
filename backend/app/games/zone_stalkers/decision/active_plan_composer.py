from __future__ import annotations

from typing import Any

from app.games.zone_stalkers.decision.models.plan import (
    Plan,
    PlanStep,
    STEP_ASK_FOR_INTEL,
    STEP_CONFIRM_KILL,
    STEP_EXPLORE_LOCATION,
    STEP_SEARCH_TARGET,
    STEP_START_COMBAT,
    STEP_TRAVEL_TO_LOCATION,
)


_STRATEGIC_COMPOSE_KEYS: frozenset[str] = frozenset({
    "FIND_ARTIFACTS",
    "GET_MONEY_FOR_RESUPPLY",
    "LOCATE_TARGET",
    "TRACK_TARGET",
    "ENGAGE_TARGET",
    "CONFIRM_KILL",
})


def _clone_step(step: PlanStep) -> PlanStep:
    return PlanStep(
        kind=step.kind,
        payload=dict(step.payload),
        interruptible=step.interruptible,
        expected_duration_ticks=step.expected_duration_ticks,
    )


def compose_active_plan_steps(
    *,
    objective_key: str,
    base_plan: Plan,
    agent: dict[str, Any],
    state: dict[str, Any],
    world_turn: int,
) -> list[PlanStep]:
    del state, world_turn
    steps = [_clone_step(step) for step in base_plan.steps]
    if objective_key not in _STRATEGIC_COMPOSE_KEYS or not steps:
        return steps

    first_step = steps[0]
    if objective_key in {"FIND_ARTIFACTS", "GET_MONEY_FOR_RESUPPLY"}:
        if any(step.kind == STEP_EXPLORE_LOCATION for step in steps):
            return steps
        if first_step.kind != STEP_TRAVEL_TO_LOCATION:
            return steps
        target_id = (
            first_step.payload.get("location_id")
            or first_step.payload.get("target_id")
            or first_step.payload.get("final_target_id")
        )
        if target_id is None:
            return steps

        steps.append(
            PlanStep(
                kind=STEP_EXPLORE_LOCATION,
                payload={
                    "target_id": target_id,
                    "location_id": target_id,
                    "reason": "active_plan_composed_explore_after_travel",
                },
                interruptible=True,
                expected_duration_ticks=30,
            )
        )
        return steps

    if first_step.kind != STEP_TRAVEL_TO_LOCATION:
        return steps

    target_id = (
        first_step.payload.get("location_id")
        or first_step.payload.get("target_id")
        or first_step.payload.get("final_target_id")
    )
    if target_id is None:
        return steps

    if objective_key == "LOCATE_TARGET" and not any(s.kind == STEP_ASK_FOR_INTEL for s in steps):
        steps.append(
            PlanStep(
                kind=STEP_ASK_FOR_INTEL,
                payload={
                    "target_id": agent.get("kill_target_id"),
                    "reason": "active_plan_composed_locate_after_travel",
                },
                interruptible=True,
                expected_duration_ticks=1,
            )
        )
        return steps

    if objective_key == "TRACK_TARGET" and not any(s.kind == STEP_SEARCH_TARGET for s in steps):
        steps.append(
            PlanStep(
                kind=STEP_SEARCH_TARGET,
                payload={
                    "target_id": agent.get("kill_target_id"),
                    "target_location_id": target_id,
                    "reason": "active_plan_composed_search_after_travel",
                },
                interruptible=True,
                expected_duration_ticks=1,
            )
        )
        return steps

    if objective_key == "ENGAGE_TARGET" and not any(s.kind == STEP_START_COMBAT for s in steps):
        steps.append(
            PlanStep(
                kind=STEP_START_COMBAT,
                payload={
                    "target_id": agent.get("kill_target_id"),
                    "reason": "active_plan_composed_engage_after_travel",
                },
                interruptible=False,
                expected_duration_ticks=1,
            )
        )
        steps.append(
            PlanStep(
                kind=STEP_CONFIRM_KILL,
                payload={
                    "target_id": agent.get("kill_target_id"),
                    "reason": "active_plan_composed_confirm_after_engage",
                },
                interruptible=False,
                expected_duration_ticks=1,
            )
        )
        return steps

    return steps
