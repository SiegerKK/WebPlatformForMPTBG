from __future__ import annotations

from app.games.zone_stalkers.decision.models.objective import Objective
from app.games.zone_stalkers.decision.objectives.selection import choose_objective


def _objective(key: str, urgency: float, expected_value: float, *, blocking: bool = False) -> Objective:
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


def test_minor_resupply_does_not_interrupt_current_travel() -> None:
    decision = choose_objective(
        [
            _objective("CONTINUE_CURRENT_PLAN", urgency=0.70, expected_value=0.80),
            _objective("RESUPPLY_FOOD", urgency=0.58, expected_value=0.65),
        ],
        personality={"risk_tolerance": 0.5},
        switch_threshold=0.10,
    )
    assert decision.selected.key == "CONTINUE_CURRENT_PLAN"
    assert decision.switch_decision == "continue_current"


def test_critical_thirst_interrupts_current_plan() -> None:
    decision = choose_objective(
        [
            _objective("CONTINUE_CURRENT_PLAN", urgency=0.70, expected_value=0.75),
            _objective("RESTORE_WATER", urgency=0.95, expected_value=1.0, blocking=True),
        ],
        personality={"risk_tolerance": 0.5},
        switch_threshold=0.10,
    )
    assert decision.selected.key == "RESTORE_WATER"
    assert decision.switch_decision == "switch"
