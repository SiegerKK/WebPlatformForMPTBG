from __future__ import annotations

from typing import Any

from app.games.zone_stalkers.decision.models.objective import Objective, ObjectiveScore


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _factor(key: str, label: str, value: float, weight: float) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "value": round(float(value), 3),
        "weight": round(float(weight), 3),
    }


def _penalty(key: str, label: str, value: float, weight: float | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "key": key,
        "label": label,
        "value": round(float(value), 3),
    }
    if weight is not None:
        payload["weight"] = round(float(weight), 3)
    return payload


def score_objective(objective: Objective, personality: dict[str, Any] | None = None) -> ObjectiveScore:
    """Score one objective using PR4 deterministic utility model."""
    profile = personality or {}
    risk_tolerance = _clamp(float(profile.get("risk_tolerance", 0.5)))
    risk_sensitivity = 1.0 - risk_tolerance

    weighted_urgency = objective.urgency * 0.35
    weighted_value = objective.expected_value * 0.20
    weighted_confidence = objective.confidence * 0.10
    weighted_memory = objective.memory_confidence * 0.10
    weighted_alignment = objective.goal_alignment * 0.10

    weighted_risk = objective.risk * risk_sensitivity
    weighted_time = objective.time_cost * 0.10
    weighted_resource = objective.resource_cost * 0.05

    blocked_penalty = 0.0
    blocker_penalties: list[dict[str, Any]] = []
    blockers = objective.metadata.get("blockers") if isinstance(objective.metadata, dict) else None
    if isinstance(blockers, list):
        for blocker in blockers:
            if not isinstance(blocker, dict):
                continue
            if not blocker.get("blocked"):
                continue
            blocker_value = _clamp(float(blocker.get("penalty", 0.35)), 0.0, 1.0)
            blocked_penalty += blocker_value
            blocker_penalties.append(
                _penalty(
                    f"blocker:{blocker.get('key', 'unknown')}",
                    str(blocker.get("reason") or blocker.get("key") or "Blocker"),
                    blocker_value,
                )
            )
    blocked_penalty = min(1.0, blocked_penalty)

    raw_score = (
        weighted_urgency
        + weighted_value
        + weighted_confidence
        + weighted_memory
        + weighted_alignment
        - weighted_risk
        - weighted_time
        - weighted_resource
    )
    final_score = max(0.0, raw_score - blocked_penalty)

    factors = (
        _factor("urgency", "Срочность", objective.urgency, 0.35),
        _factor("expected_value", "Ожидаемая ценность", objective.expected_value, 0.20),
        _factor("confidence", "Уверенность", objective.confidence, 0.10),
        _factor("memory_confidence", "Надёжность памяти", objective.memory_confidence, 0.10),
        _factor("goal_alignment", "Соответствие цели", objective.goal_alignment, 0.10),
    )

    penalties: list[dict[str, Any]] = [
        _penalty("risk", "Риск", weighted_risk, risk_sensitivity),
        _penalty("time_cost", "Временная цена", weighted_time, 0.10),
        _penalty("resource_cost", "Ресурсная цена", weighted_resource, 0.05),
    ]
    if blocked_penalty > 0:
        penalties.append(_penalty("blocked_penalty", "Блокирующие условия", blocked_penalty))
        penalties.extend(blocker_penalties)

    return ObjectiveScore(
        objective_key=objective.key,
        raw_score=round(raw_score, 6),
        final_score=round(final_score, 6),
        factors=tuple(factors),
        penalties=tuple(penalties),
    )


def score_objectives(
    objectives: list[Objective],
    personality: dict[str, Any] | None = None,
) -> list[tuple[Objective, ObjectiveScore]]:
    return [(objective, score_objective(objective, personality=personality)) for objective in objectives]
