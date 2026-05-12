from __future__ import annotations

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.models.intent import Intent
from app.games.zone_stalkers.decision.executors import execute_plan_step
from app.games.zone_stalkers.decision.models.plan import (
    Plan,
    PlanStep,
    STEP_TRADE_BUY_ITEM,
    STEP_TRADE_SELL_ITEM,
    STEP_CONSUME_ITEM,
)
from app.games.zone_stalkers.decision.needs import evaluate_need_result
from app.games.zone_stalkers.decision.planner import _plan_seek_consumable
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


def test_critical_thirst_plan_includes_sell_buy_consume_chain_when_unaffordable() -> None:
    agent = make_agent(
        money=0,
        thirst=100,
        inventory=[{"id": "artifact_1", "type": "soul", "name": "Soul", "value": 2000}],
        has_weapon=False,
        has_armor=False,
        has_ammo=False,
    )
    state = make_state_with_trader(agent=agent, trader_at="loc_a")
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    intent = Intent(kind="seek_water", score=1.0)

    plan = _plan_seek_consumable(ctx, intent, state, 100, need_result)
    assert plan is not None
    step_kinds = [step.kind for step in plan.steps]
    assert STEP_TRADE_SELL_ITEM in step_kinds
    assert STEP_TRADE_BUY_ITEM in step_kinds
    assert STEP_CONSUME_ITEM in step_kinds
