from __future__ import annotations

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.debug.brain_trace import write_decision_brain_trace_from_v2
from app.games.zone_stalkers.decision.needs import evaluate_need_result
from tests.decision.conftest import make_agent, make_minimal_state


def test_brain_trace_decision_contains_immediate_item_liquidity_blocks() -> None:
    agent = make_agent(
        thirst=85,
        hunger=86,
        has_weapon=False,
        inventory=[
            {"id": "w", "type": "water", "value": 30},
            {"id": "b", "type": "bread", "value": 20},
        ],
    )
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)

    write_decision_brain_trace_from_v2(
        agent,
        world_turn=state["world_turn"],
        intent_kind="seek_water",
        intent_score=0.9,
        reason="critical_thirst",
        state=state,
        need_result=need_result,
    )

    ev = agent["brain_trace"]["events"][-1]
    assert "immediate_needs" in ev
    assert "item_needs" in ev
    assert "liquidity" in ev
    assert len(ev["immediate_needs"]) <= 3
    assert len(ev["item_needs"]) <= 5
