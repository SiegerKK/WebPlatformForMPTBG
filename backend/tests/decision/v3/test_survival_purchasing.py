from __future__ import annotations

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.executors import execute_plan_step
from app.games.zone_stalkers.decision.models.plan import Plan, PlanStep, STEP_TRADE_BUY_ITEM
from tests.decision.conftest import make_agent, make_state_with_trader


def test_survival_buy_food_prefers_cheapest_affordable_item() -> None:
    agent = make_agent(money=200, inventory=[])
    state = make_state_with_trader(agent=agent, trader_at="loc_a")
    ctx = build_agent_context("bot1", agent, state)

    plan = Plan(
        intent_kind="seek_food",
        steps=[
            PlanStep(
                kind=STEP_TRADE_BUY_ITEM,
                payload={
                    "item_category": "food",
                    "buy_mode": "survival_cheapest",
                    "compatible_item_types": ["bread", "glucose"],
                    "reason": "buy_food_survival",
                },
            )
        ],
        created_turn=100,
    )

    events = execute_plan_step(ctx, plan, state, 100)
    assert events
    bought = events[0]["payload"]["item_type"]
    assert bought == "bread"
