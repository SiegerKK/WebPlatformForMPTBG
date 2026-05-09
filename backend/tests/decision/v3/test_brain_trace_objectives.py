from __future__ import annotations

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.debug.brain_trace import write_decision_brain_trace_from_v2
from app.games.zone_stalkers.decision.needs import evaluate_need_result
from tests.decision.conftest import make_agent, make_minimal_state


def test_brain_trace_contains_objective_summary_and_caps_lists() -> None:
    agent = make_agent(thirst=90, has_weapon=False)
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)

    objective_scores = [
        {"key": f"OBJ_{idx}", "score": 0.9 - idx * 0.05, "decision": "rejected", "reason": "reason"}
        for idx in range(7)
    ]
    alternatives = [
        {"key": f"ALT_{idx}", "score": 0.8 - idx * 0.04, "decision": "rejected", "reason": "alt reason"}
        for idx in range(7)
    ]

    write_decision_brain_trace_from_v2(
        agent,
        world_turn=state["world_turn"],
        intent_kind="resupply",
        intent_score=0.65,
        reason="objective selected",
        state=state,
        need_result=need_result,
        active_objective={"key": "RESUPPLY_WEAPON", "score": 0.65, "source": "item_need", "reason": "Нет оружия"},
        objective_scores=objective_scores,
        alternatives=alternatives,
    )

    ev = agent["brain_trace"]["events"][-1]
    assert ev["active_objective"]["key"] == "RESUPPLY_WEAPON"
    assert len(ev["objective_scores"]) == 5
    assert len(ev["alternatives"]) == 5
