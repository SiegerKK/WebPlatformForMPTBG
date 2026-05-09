from __future__ import annotations

from app.games.zone_stalkers.decision.active_plan_composer import compose_active_plan_steps
from app.games.zone_stalkers.decision.models.plan import (
    Plan,
    PlanStep,
    STEP_EXPLORE_LOCATION,
    STEP_TRAVEL_TO_LOCATION,
    STEP_TRADE_SELL_ITEM,
)


def _base_plan(*steps: PlanStep) -> Plan:
    return Plan(intent_kind="test", steps=list(steps), created_turn=100)


def test_find_artifacts_composes_travel_and_explore_steps() -> None:
    steps = compose_active_plan_steps(
        objective_key="FIND_ARTIFACTS",
        base_plan=_base_plan(
            PlanStep(STEP_TRAVEL_TO_LOCATION, {"target_id": "loc_b"}, expected_duration_ticks=12),
        ),
        agent={},
        state={},
        world_turn=100,
    )
    assert [step.kind for step in steps] == [STEP_TRAVEL_TO_LOCATION, STEP_EXPLORE_LOCATION]
    assert steps[1].payload.get("target_id") == "loc_b"


def test_get_money_for_resupply_composes_travel_and_explore_steps() -> None:
    steps = compose_active_plan_steps(
        objective_key="GET_MONEY_FOR_RESUPPLY",
        base_plan=_base_plan(
            PlanStep(STEP_TRAVEL_TO_LOCATION, {"target_id": "loc_c"}, expected_duration_ticks=14),
        ),
        agent={},
        state={},
        world_turn=100,
    )
    assert [step.kind for step in steps] == [STEP_TRAVEL_TO_LOCATION, STEP_EXPLORE_LOCATION]
    assert steps[1].payload.get("target_id") == "loc_c"


def test_sell_artifacts_remains_travel_then_sell() -> None:
    original = _base_plan(
        PlanStep(STEP_TRAVEL_TO_LOCATION, {"target_id": "loc_trader"}),
        PlanStep(STEP_TRADE_SELL_ITEM, {"item_category": "artifact"}),
    )
    steps = compose_active_plan_steps(
        objective_key="SELL_ARTIFACTS",
        base_plan=original,
        agent={},
        state={},
        world_turn=100,
    )
    assert [step.kind for step in steps] == [STEP_TRAVEL_TO_LOCATION, STEP_TRADE_SELL_ITEM]


def test_atomic_restore_water_plan_can_stay_one_step() -> None:
    original = _base_plan(
        PlanStep("consume_item", {"item_type": "water"}),
    )
    steps = compose_active_plan_steps(
        objective_key="RESTORE_WATER",
        base_plan=original,
        agent={},
        state={},
        world_turn=100,
    )
    assert [step.kind for step in steps] == ["consume_item"]
