from __future__ import annotations

from app.games.zone_stalkers.decision.models.objective import Objective, ObjectiveDecision, ObjectiveScore
from .generator import OBJECTIVE_CONTINUE_CURRENT_PLAN
from .scoring import score_objectives

_MAINTENANCE_OBJECTIVE_KEYS: set[str] = {
    "RESTORE_FOOD",
    "RESTORE_WATER",
    "REST",
}

_STRATEGIC_OBJECTIVE_KEYS: set[str] = {
    "GET_MONEY_FOR_RESUPPLY",
    "FIND_ARTIFACTS",
    "SELL_ARTIFACTS",
    "RESUPPLY_WEAPON",
    "RESUPPLY_AMMO",
    "HUNT_TARGET",
    "SEARCH_INFORMATION",
    "LEAVE_ZONE",
}

_MAINTENANCE_REBALANCE_MARGIN = 0.10


def _is_blocking(objective: Objective) -> bool:
    if not isinstance(objective.metadata, dict):
        return False
    return bool(objective.metadata.get("is_blocking"))


def choose_objective(
    objectives: list[Objective],
    personality: dict | None = None,
    switch_threshold: float = 0.10,
) -> ObjectiveDecision:
    """Pick objective with continue-vs-switch handling and blocking override."""
    if not objectives:
        raise ValueError("choose_objective requires at least one objective")

    scored = score_objectives(objectives, personality=personality)
    scored_sorted = sorted(scored, key=lambda pair: pair[1].final_score, reverse=True)

    continue_pair = next(
        (pair for pair in scored_sorted if pair[0].key == OBJECTIVE_CONTINUE_CURRENT_PLAN),
        None,
    )
    continue_score: ObjectiveScore | None = continue_pair[1] if continue_pair else None

    best_pair = scored_sorted[0]
    best_new_pair = next((pair for pair in scored_sorted if pair[0].key != OBJECTIVE_CONTINUE_CURRENT_PLAN), None)

    selected_pair = best_pair
    switch_decision = "new_objective"
    reason = "Выбрана лучшая новая цель"

    if continue_pair and best_new_pair:
        continue_value = continue_pair[1].final_score
        new_value = best_new_pair[1].final_score
        if _is_blocking(best_new_pair[0]):
            selected_pair = best_new_pair
            switch_decision = "switch"
            reason = "Блокирующая цель прерывает текущий план"
        elif new_value <= continue_value + switch_threshold:
            selected_pair = continue_pair
            switch_decision = "continue_current"
            reason = "Преимущество новой цели ниже порога переключения"
        else:
            selected_pair = best_new_pair
            switch_decision = "switch"
            reason = "Новая цель существенно лучше текущего плана"
    elif continue_pair and best_pair[0].key == OBJECTIVE_CONTINUE_CURRENT_PLAN:
        switch_decision = "continue_current"
        reason = "Текущий план остаётся лучшим"

    selected_objective, selected_score = selected_pair
    if (
        selected_objective.key in _MAINTENANCE_OBJECTIVE_KEYS
        and not _is_blocking(selected_objective)
        and not bool((selected_objective.metadata or {}).get("critical"))
        and float(selected_objective.urgency) < 0.5
    ):
        strategic_pair = next(
            (pair for pair in scored_sorted if pair[0].key in _STRATEGIC_OBJECTIVE_KEYS),
            None,
        )
        if strategic_pair is not None:
            strategic_objective, strategic_score = strategic_pair
            if (selected_score.final_score - strategic_score.final_score) < _MAINTENANCE_REBALANCE_MARGIN:
                selected_objective, selected_score = strategic_objective, strategic_score
                selected_pair = strategic_pair
                switch_decision = "switch"
                reason = "Стратегическая цель почти равна по скору и приоритетнее поддерживающей"

    alternatives = tuple(
        (objective, score)
        for objective, score in scored_sorted
        if objective.key != selected_objective.key
    )

    selected_score = ObjectiveScore(
        objective_key=selected_score.objective_key,
        raw_score=selected_score.raw_score,
        final_score=selected_score.final_score,
        factors=selected_score.factors,
        penalties=selected_score.penalties,
        decision="selected",
    )

    adjusted_alternatives: list[tuple[Objective, ObjectiveScore]] = []
    for objective, score in alternatives:
        decision = "continue_current" if objective.key == OBJECTIVE_CONTINUE_CURRENT_PLAN else "rejected"
        adjusted_alternatives.append(
            (
                objective,
                ObjectiveScore(
                    objective_key=score.objective_key,
                    raw_score=score.raw_score,
                    final_score=score.final_score,
                    factors=score.factors,
                    penalties=score.penalties,
                    decision=decision,
                ),
            )
        )

    return ObjectiveDecision(
        selected=selected_objective,
        selected_score=selected_score,
        alternatives=tuple(adjusted_alternatives),
        continue_current_score=continue_score,
        switch_decision=switch_decision,
        reason=reason,
    )
