from __future__ import annotations

from app.games.zone_stalkers.decision.models.objective import Objective
from app.games.zone_stalkers.decision.objectives.scoring import score_objective


def _objective(key: str, **kwargs) -> Objective:
    base = dict(
        source="test",
        urgency=0.5,
        expected_value=0.5,
        risk=0.2,
        time_cost=0.2,
        resource_cost=0.1,
        confidence=0.8,
        goal_alignment=0.8,
        memory_confidence=0.8,
        reasons=("test",),
    )
    base.update(kwargs)
    return Objective(key=key, **base)


def test_scoring_prioritizes_critical_thirst_over_rest() -> None:
    restore_water = _objective("RESTORE_WATER", urgency=0.95, expected_value=1.0, risk=0.05, time_cost=0.05)
    rest = _objective("REST", urgency=0.45, expected_value=0.6, risk=0.05, time_cost=0.4)

    water_score = score_objective(restore_water, personality={"risk_tolerance": 0.5})
    rest_score = score_objective(rest, personality={"risk_tolerance": 0.5})

    assert water_score.final_score > rest_score.final_score


def test_risk_tolerance_changes_risky_objective_score() -> None:
    risky = _objective("FIND_ARTIFACTS", urgency=0.7, expected_value=0.9, risk=0.8)

    cautious = score_objective(risky, personality={"risk_tolerance": 0.1})
    brave = score_objective(risky, personality={"risk_tolerance": 0.9})

    assert brave.final_score > cautious.final_score


def test_blocker_penalty_reduces_final_score_and_is_traced() -> None:
    engage = _objective(
        "ENGAGE_TARGET",
        urgency=0.8,
        expected_value=0.95,
        risk=0.7,
        metadata={
            "blockers": [
                {"key": "no_weapon", "reason": "Нет оружия", "blocked": True, "penalty": 0.6},
                {"key": "low_ammo", "reason": "Нет патронов", "blocked": True, "penalty": 0.4},
            ]
        },
    )

    score = score_objective(engage, personality={"risk_tolerance": 0.5})

    assert score.final_score < score.raw_score
    assert any(p.get("key") == "blocked_penalty" for p in score.penalties)
    assert any(p.get("key") == "blocker:no_weapon" for p in score.penalties)
