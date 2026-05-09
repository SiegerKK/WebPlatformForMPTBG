from __future__ import annotations

from app.games.zone_stalkers.decision.models.objective import Objective, ObjectiveScore
from app.games.zone_stalkers.decision.models.intent import (
    INTENT_GET_RICH,
    INTENT_RESUPPLY,
    INTENT_SEEK_WATER,
)
from app.games.zone_stalkers.decision.objectives.intent_adapter import objective_to_intent


def _objective(key: str) -> Objective:
    return Objective(
        key=key,
        source="test",
        urgency=0.7,
        expected_value=0.8,
        risk=0.1,
        time_cost=0.1,
        resource_cost=0.1,
        confidence=0.9,
        goal_alignment=0.9,
        memory_confidence=0.8,
        reasons=("reason",),
        metadata={},
    )


def _score(key: str, final: float = 0.7) -> ObjectiveScore:
    return ObjectiveScore(
        objective_key=key,
        raw_score=final,
        final_score=final,
        factors=(),
        penalties=(),
    )


def test_restore_water_maps_to_seek_water_with_metadata() -> None:
    objective = _objective("RESTORE_WATER")
    intent = objective_to_intent(objective, _score("RESTORE_WATER", 0.91), world_turn=100)

    assert intent.kind == INTENT_SEEK_WATER
    assert intent.metadata and intent.metadata["objective_key"] == "RESTORE_WATER"
    assert intent.metadata["objective_score"] == 0.91


def test_resupply_weapon_maps_to_resupply() -> None:
    intent = objective_to_intent(_objective("RESUPPLY_WEAPON"), _score("RESUPPLY_WEAPON", 0.65), world_turn=100)
    assert intent.kind == INTENT_RESUPPLY
    assert intent.metadata and intent.metadata.get("forced_resupply_category") == "weapon"


def test_resupply_food_maps_to_resupply_with_forced_food_category() -> None:
    intent = objective_to_intent(_objective("RESUPPLY_FOOD"), _score("RESUPPLY_FOOD", 0.7), world_turn=100)
    assert intent.kind == INTENT_RESUPPLY
    assert intent.metadata and intent.metadata.get("forced_resupply_category") == "food"


def test_get_money_for_resupply_maps_to_get_rich() -> None:
    intent = objective_to_intent(_objective("GET_MONEY_FOR_RESUPPLY"), _score("GET_MONEY_FOR_RESUPPLY", 0.6), world_turn=100)
    assert intent.kind == INTENT_GET_RICH
