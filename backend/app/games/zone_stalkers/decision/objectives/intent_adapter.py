from __future__ import annotations

from app.games.zone_stalkers.decision.models.intent import (
    INTENT_ESCAPE_DANGER,
    INTENT_FLEE_EMISSION,
    INTENT_GET_RICH,
    INTENT_HEAL_SELF,
    INTENT_HUNT_TARGET,
    INTENT_IDLE,
    INTENT_LEAVE_ZONE,
    INTENT_REST,
    INTENT_RESUPPLY,
    INTENT_SEARCH_INFORMATION,
    INTENT_SEEK_FOOD,
    INTENT_SEEK_WATER,
    INTENT_SELL_ARTIFACTS,
    INTENT_WAIT_IN_SHELTER,
    Intent,
)
from app.games.zone_stalkers.decision.models.objective import Objective, ObjectiveScore


OBJECTIVE_TO_INTENT: dict[str, str] = {
    "RESTORE_WATER": INTENT_SEEK_WATER,
    "RESTORE_FOOD": INTENT_SEEK_FOOD,
    "HEAL_SELF": INTENT_HEAL_SELF,
    "REST": INTENT_REST,
    "RESUPPLY_WEAPON": INTENT_RESUPPLY,
    "RESUPPLY_ARMOR": INTENT_RESUPPLY,
    "RESUPPLY_AMMO": INTENT_RESUPPLY,
    "RESUPPLY_FOOD": INTENT_RESUPPLY,
    "RESUPPLY_DRINK": INTENT_RESUPPLY,
    "RESUPPLY_MEDICINE": INTENT_RESUPPLY,
    "GET_MONEY_FOR_RESUPPLY": INTENT_GET_RICH,
    "REACH_SAFE_SHELTER": INTENT_FLEE_EMISSION,
    "WAIT_IN_SHELTER": INTENT_WAIT_IN_SHELTER,
    "ESCAPE_DANGER": INTENT_ESCAPE_DANGER,
    "FIND_ARTIFACTS": INTENT_GET_RICH,
    "SELL_ARTIFACTS": INTENT_SELL_ARTIFACTS,
    "HUNT_TARGET": INTENT_HUNT_TARGET,
    "LOCATE_TARGET": INTENT_HUNT_TARGET,
    "PREPARE_FOR_HUNT": INTENT_RESUPPLY,
    "TRACK_TARGET": INTENT_HUNT_TARGET,
    "INTERCEPT_TARGET": INTENT_HUNT_TARGET,
    "AMBUSH_TARGET": INTENT_HUNT_TARGET,
    "ENGAGE_TARGET": INTENT_HUNT_TARGET,
    "CONFIRM_KILL": INTENT_HUNT_TARGET,
    "RETREAT_FROM_TARGET": INTENT_ESCAPE_DANGER,
    "RECOVER_AFTER_COMBAT": INTENT_HEAL_SELF,
    "SEARCH_INFORMATION": INTENT_SEARCH_INFORMATION,
    "LEAVE_ZONE": INTENT_LEAVE_ZONE,
    "IDLE": INTENT_IDLE,
    "CONTINUE_CURRENT_PLAN": INTENT_IDLE,
}


def _objective_reason(objective: Objective) -> str:
    if objective.reasons:
        return "; ".join(str(r) for r in objective.reasons if r)
    return f"Objective {objective.key} выбран"


def objective_to_intent(
    objective: Objective,
    score: ObjectiveScore,
    *,
    world_turn: int,
    source_goal: str | None = None,
) -> Intent:
    intent_kind = OBJECTIVE_TO_INTENT.get(objective.key, INTENT_IDLE)
    metadata = {
        "objective_key": objective.key,
        "objective_score": round(float(score.final_score), 3),
        "objective_source": objective.source,
        "objective_reasons": list(objective.reasons),
        "objective_target": objective.target,
        "objective_blockers": objective.metadata.get("blockers") if isinstance(objective.metadata, dict) else None,
    }
    return Intent(
        kind=intent_kind,
        score=float(score.final_score),
        source_goal=source_goal,
        target_id=(objective.target or {}).get("target_id") if isinstance(objective.target, dict) else None,
        target_location_id=(objective.target or {}).get("location_id") if isinstance(objective.target, dict) else None,
        reason=_objective_reason(objective),
        created_turn=world_turn,
        metadata=metadata,
    )
