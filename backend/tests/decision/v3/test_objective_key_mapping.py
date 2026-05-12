"""Fix 4 — UNKNOWN objective keys must not appear for common intent kinds.

Tests that _INTENT_TO_OBJECTIVE_KEY_FALLBACK covers the key intent kinds
that were previously mapping to "UNKNOWN".
"""
from __future__ import annotations

from app.games.zone_stalkers.decision.models.intent import Intent
from app.games.zone_stalkers.rules.tick_rules import (
    _INTENT_TO_OBJECTIVE_KEY_FALLBACK,
    _fallback_objective_key_for_intent,
)


def test_flee_emission_maps_to_reach_safe_shelter() -> None:
    assert _INTENT_TO_OBJECTIVE_KEY_FALLBACK.get("flee_emission") == "REACH_SAFE_SHELTER"


def test_wait_in_shelter_maps_to_wait_in_shelter() -> None:
    assert _INTENT_TO_OBJECTIVE_KEY_FALLBACK.get("wait_in_shelter") == "WAIT_IN_SHELTER"


def test_seek_water_maps_to_restore_water() -> None:
    assert _INTENT_TO_OBJECTIVE_KEY_FALLBACK.get("seek_water") == "RESTORE_WATER"


def test_seek_food_maps_to_restore_food() -> None:
    assert _INTENT_TO_OBJECTIVE_KEY_FALLBACK.get("seek_food") == "RESTORE_FOOD"


def test_rest_maps_to_rest() -> None:
    assert _INTENT_TO_OBJECTIVE_KEY_FALLBACK.get("rest") == "REST"


def test_no_unknown_for_common_intents() -> None:
    common_intents = [
        "flee_emission", "wait_in_shelter", "seek_water", "seek_food",
        "rest", "heal_self", "escape_danger", "sell_artifacts", "get_rich",
        "hunt_target",
    ]
    for intent in common_intents:
        mapped = _INTENT_TO_OBJECTIVE_KEY_FALLBACK.get(intent)
        assert mapped is not None, f"Intent {intent!r} has no fallback mapping"
        assert mapped != "UNKNOWN", f"Intent {intent!r} still maps to UNKNOWN"


def test_escape_danger_maps_to_escape_danger() -> None:
    assert _INTENT_TO_OBJECTIVE_KEY_FALLBACK.get("escape_danger") == "ESCAPE_DANGER"


def test_hunt_target_maps_to_hunt_target() -> None:
    assert _INTENT_TO_OBJECTIVE_KEY_FALLBACK.get("hunt_target") == "HUNT_TARGET"


def test_resupply_forced_category_uses_specific_objective() -> None:
    intent = Intent(kind="resupply", score=0.7, metadata={"forced_resupply_category": "ammo"})
    assert _fallback_objective_key_for_intent(intent) == "RESUPPLY_AMMO"


def test_resupply_without_forced_category_has_no_fallback_objective() -> None:
    intent = Intent(kind="resupply", score=0.7, metadata={})
    assert _fallback_objective_key_for_intent(intent) is None
