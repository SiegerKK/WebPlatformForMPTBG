from __future__ import annotations

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.models.intent import Intent
from app.games.zone_stalkers.decision.models.plan import (
    STEP_CONSUME_ITEM,
    STEP_REQUEST_LOAN,
    STEP_TRADE_BUY_ITEM,
    STEP_TRADE_SELL_ITEM,
)
from app.games.zone_stalkers.decision.needs import evaluate_need_result
from app.games.zone_stalkers.decision.planner import _plan_heal_or_flee, _plan_seek_consumable
from tests.decision.conftest import make_agent, make_state_with_trader


def _prepare_state(agent: dict) -> dict:
    state = make_state_with_trader(agent=agent, trader_at="loc_a")
    state["traders"]["trader_1"]["id"] = "trader_1"
    state["traders"]["trader_1"]["money"] = 0
    state["traders"]["trader_1"]["accounts_receivable"] = 0
    return state


def test_poor_thirsty_agent_at_trader_gets_loan_plan_not_sell_plan() -> None:
    agent = make_agent(
        money=20,
        thirst=100,
        inventory=[],
        has_weapon=False,
        has_armor=False,
        has_ammo=False,
    )
    state = _prepare_state(agent)
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    plan = _plan_seek_consumable(ctx, Intent(kind="seek_water", score=1.0), state, 100, need_result)
    assert plan is not None
    kinds = [step.kind for step in plan.steps]
    assert kinds[:3] == [STEP_REQUEST_LOAN, STEP_TRADE_BUY_ITEM, STEP_CONSUME_ITEM]
    assert STEP_TRADE_SELL_ITEM not in kinds


def test_poor_hungry_agent_at_trader_gets_loan_plan_not_sell_plan() -> None:
    agent = make_agent(
        money=10,
        hunger=100,
        inventory=[],
        has_weapon=False,
        has_armor=False,
        has_ammo=False,
    )
    state = _prepare_state(agent)
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    plan = _plan_seek_consumable(ctx, Intent(kind="seek_food", score=1.0), state, 100, need_result)
    assert plan is not None
    kinds = [step.kind for step in plan.steps]
    assert kinds[:3] == [STEP_REQUEST_LOAN, STEP_TRADE_BUY_ITEM, STEP_CONSUME_ITEM]
    assert STEP_TRADE_SELL_ITEM not in kinds


def test_poor_injured_agent_at_trader_gets_loan_buy_heal_consume_plan() -> None:
    agent = make_agent(
        hp=40,
        money=5,
        inventory=[],
        has_weapon=False,
        has_armor=False,
        has_ammo=False,
    )
    state = _prepare_state(agent)
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    plan = _plan_heal_or_flee(ctx, Intent(kind="heal_self", score=1.0), state, 100, need_result)
    assert plan is not None
    kinds = [step.kind for step in plan.steps]
    assert kinds[:3] == [STEP_REQUEST_LOAN, STEP_TRADE_BUY_ITEM, STEP_CONSUME_ITEM]


def test_agent_with_safe_sellable_item_sells_before_taking_loan() -> None:
    agent = make_agent(
        money=0,
        thirst=100,
        inventory=[{"id": "artifact_1", "type": "soul", "name": "Soul", "value": 2000}],
        has_weapon=False,
        has_armor=False,
        has_ammo=False,
    )
    state = _prepare_state(agent)
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    plan = _plan_seek_consumable(ctx, Intent(kind="seek_water", score=1.0), state, 100, need_result)
    assert plan is not None
    assert plan.steps[0].kind == STEP_TRADE_SELL_ITEM


def test_no_loan_for_ammo_weapon_armor_or_get_rich() -> None:
    agent = make_agent(money=0, inventory=[], has_weapon=False, has_armor=False, has_ammo=False)
    state = _prepare_state(agent)
    ctx = build_agent_context("bot1", agent, state)
    plan = _plan_seek_consumable(
        ctx,
        Intent(kind="resupply", score=1.0),
        state,
        100,
        evaluate_need_result(ctx, state),
    )
    assert plan is None or all(step.kind != STEP_REQUEST_LOAN for step in plan.steps)

