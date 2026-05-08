from __future__ import annotations

from app.games.zone_stalkers.decision.models.objective import Objective
from app.games.zone_stalkers.decision.objectives.selection import choose_objective


def _objective(key: str, urgency: float, *, expected_value: float = 0.7, blocking: bool = False) -> Objective:
    return Objective(
        key=key,
        source="test",
        urgency=urgency,
        expected_value=expected_value,
        risk=0.1,
        time_cost=0.1,
        resource_cost=0.0,
        confidence=0.8,
        goal_alignment=0.8,
        memory_confidence=0.8,
        reasons=(key,),
        metadata={"is_blocking": blocking},
    )


def test_continue_current_plan_when_new_objective_gain_is_small() -> None:
    decision = choose_objective(
        [
            _objective("CONTINUE_CURRENT_PLAN", 0.75),
            _objective("RESUPPLY_FOOD", 0.76),
        ],
        personality={"risk_tolerance": 0.5},
        switch_threshold=0.10,
    )

    assert decision.selected.key == "CONTINUE_CURRENT_PLAN"
    assert decision.switch_decision == "continue_current"


def test_blocking_objective_overrides_switch_threshold() -> None:
    decision = choose_objective(
        [
            _objective("CONTINUE_CURRENT_PLAN", 0.85),
            _objective("RESTORE_WATER", 0.80, expected_value=1.0, blocking=True),
        ],
        personality={"risk_tolerance": 0.5},
        switch_threshold=0.10,
    )

    assert decision.selected.key == "RESTORE_WATER"
    assert decision.switch_decision == "switch"
