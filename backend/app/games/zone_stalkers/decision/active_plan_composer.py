from __future__ import annotations

from typing import Any

from app.games.zone_stalkers.decision.models.plan import (
    Plan,
    PlanStep,
    STEP_EXPLORE_LOCATION,
    STEP_TRAVEL_TO_LOCATION,
)


_STRATEGIC_COMPOSE_KEYS: frozenset[str] = frozenset({
    "FIND_ARTIFACTS",
    "GET_MONEY_FOR_RESUPPLY",
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
    del agent, state, world_turn
    steps = [_clone_step(step) for step in base_plan.steps]
    if objective_key not in _STRATEGIC_COMPOSE_KEYS or not steps:
        return steps

    if any(step.kind == STEP_EXPLORE_LOCATION for step in steps):
        return steps

    first_step = steps[0]
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
