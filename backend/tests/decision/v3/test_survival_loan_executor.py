from __future__ import annotations

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.executors import execute_plan_step
from app.games.zone_stalkers.decision.models.plan import (
    Plan,
    PlanStep,
    STEP_CONSUME_ITEM,
    STEP_REQUEST_LOAN,
    STEP_TRADE_BUY_ITEM,
)
from tests.decision.conftest import make_agent, make_state_with_trader


def _state(agent: dict) -> dict:
    state = make_state_with_trader(agent=agent, trader_at="loc_a")
    trader = state["traders"]["trader_1"]
    trader["id"] = "trader_1"
    trader["money"] = 0
    trader["accounts_receivable"] = 0
    return state


def test_request_loan_success_creates_debt_and_accounts_receivable() -> None:
    agent = make_agent(money=10, thirst=100, inventory=[], has_weapon=False, has_armor=False, has_ammo=False)
    state = _state(agent)
    plan = Plan(
        intent_kind="seek_water",
        steps=[PlanStep(
            kind=STEP_REQUEST_LOAN,
            payload={
                "creditor_id": "trader_1",
                "creditor_type": "trader",
                "amount": 35,
                "purpose": "survival_drink",
                "item_category": "drink",
                "required_price": 45,
                "daily_interest_rate": 0.05,
            },
            interruptible=False,
        )],
    )
    ctx = build_agent_context("bot1", agent, state)
    events = execute_plan_step(ctx, plan, state, 100)
    assert any(ev.get("event_type") == "debt_created" for ev in events)
    assert int(agent.get("money") or 0) == 45
    assert int(state["traders"]["trader_1"].get("accounts_receivable") or 0) == 35
    assert int(state["traders"]["trader_1"].get("money") or 0) == 0
    assert state.get("debt_ledger", {}).get("debts")


def test_request_loan_does_not_require_trader_cash() -> None:
    agent = make_agent(money=0, thirst=100, inventory=[], has_weapon=False, has_armor=False, has_ammo=False)
    state = _state(agent)
    state["traders"]["trader_1"]["money"] = 0
    plan = Plan(
        intent_kind="seek_water",
        steps=[PlanStep(
            kind=STEP_REQUEST_LOAN,
            payload={
                "creditor_id": "trader_1",
                "creditor_type": "trader",
                "amount": 45,
                "purpose": "survival_drink",
                "item_category": "drink",
                "required_price": 45,
                "daily_interest_rate": 0.05,
            },
            interruptible=False,
        )],
    )
    events = execute_plan_step(build_agent_context("bot1", agent, state), plan, state, 100)
    assert any(ev.get("event_type") == "debt_created" for ev in events)
    assert int(agent.get("money") or 0) == 45


def test_failed_request_loan_does_not_advance_plan_to_trade_buy() -> None:
    agent = make_agent(money=0, thirst=100, inventory=[], has_weapon=False, has_armor=False, has_ammo=False)
    state = _state(agent)
    plan = Plan(
        intent_kind="seek_water",
        steps=[
            PlanStep(
                kind=STEP_REQUEST_LOAN,
                payload={
                    "creditor_id": "trader_1",
                    "creditor_type": "trader",
                    "amount": 1000,
                    "purpose": "survival_drink",
                    "item_category": "drink",
                    "required_price": 1000,
                    "daily_interest_rate": 0.05,
                },
                interruptible=False,
            ),
            PlanStep(kind=STEP_TRADE_BUY_ITEM, payload={"item_category": "drink"}, interruptible=False),
        ],
        current_step_index=0,
    )
    execute_plan_step(build_agent_context("bot1", agent, state), plan, state, 100)
    assert plan.current_step_index == 0
    assert plan.steps[0].payload.get("_loan_failed") is True


def test_request_loan_then_trade_buy_then_consume_water() -> None:
    agent = make_agent(money=0, thirst=100, inventory=[], has_weapon=False, has_armor=False, has_ammo=False)
    state = _state(agent)
    plan = Plan(
        intent_kind="seek_water",
        steps=[
            PlanStep(
                kind=STEP_REQUEST_LOAN,
                payload={
                    "creditor_id": "trader_1",
                    "creditor_type": "trader",
                    "amount": 45,
                    "purpose": "survival_drink",
                    "item_category": "drink",
                    "required_price": 45,
                    "daily_interest_rate": 0.05,
                },
                interruptible=False,
            ),
            PlanStep(kind=STEP_TRADE_BUY_ITEM, payload={"item_category": "drink", "buy_mode": "survival_cheapest"}, interruptible=False),
            PlanStep(kind=STEP_CONSUME_ITEM, payload={"item_type": "water", "reason": "emergency_drink"}, interruptible=False),
        ],
        current_step_index=0,
    )
    thirst_before = int(agent.get("thirst") or 0)
    while not plan.is_complete:
        execute_plan_step(build_agent_context("bot1", agent, state), plan, state, 100 + plan.current_step_index)
    assert int(agent.get("thirst") or 0) < thirst_before


def test_request_loan_then_trade_buy_then_consume_food() -> None:
    agent = make_agent(money=0, hunger=100, inventory=[], has_weapon=False, has_armor=False, has_ammo=False)
    state = _state(agent)
    plan = Plan(
        intent_kind="seek_food",
        steps=[
            PlanStep(
                kind=STEP_REQUEST_LOAN,
                payload={
                    "creditor_id": "trader_1",
                    "creditor_type": "trader",
                    "amount": 45,
                    "purpose": "survival_food",
                    "item_category": "food",
                    "required_price": 45,
                    "daily_interest_rate": 0.05,
                },
                interruptible=False,
            ),
            PlanStep(kind=STEP_TRADE_BUY_ITEM, payload={"item_category": "food", "buy_mode": "survival_cheapest"}, interruptible=False),
            PlanStep(kind=STEP_CONSUME_ITEM, payload={"item_type": "bread", "reason": "emergency_food"}, interruptible=False),
        ],
        current_step_index=0,
    )
    hunger_before = int(agent.get("hunger") or 0)
    while not plan.is_complete:
        execute_plan_step(build_agent_context("bot1", agent, state), plan, state, 100 + plan.current_step_index)
    assert int(agent.get("hunger") or 0) < hunger_before


def test_request_loan_then_trade_buy_then_consume_medical() -> None:
    agent = make_agent(money=0, hp=40, inventory=[], has_weapon=False, has_armor=False, has_ammo=False)
    state = _state(agent)
    plan = Plan(
        intent_kind="heal_self",
        steps=[
            PlanStep(
                kind=STEP_REQUEST_LOAN,
                payload={
                    "creditor_id": "trader_1",
                    "creditor_type": "trader",
                    "amount": 45,
                    "purpose": "survival_medical",
                    "item_category": "medical",
                    "required_price": 45,
                    "daily_interest_rate": 0.05,
                },
                interruptible=False,
            ),
            PlanStep(kind=STEP_TRADE_BUY_ITEM, payload={"item_category": "medical", "buy_mode": "survival_cheapest"}, interruptible=False),
            PlanStep(kind=STEP_CONSUME_ITEM, payload={"item_type": "bandage", "reason": "emergency_heal"}, interruptible=False),
        ],
        current_step_index=0,
    )
    hp_before = int(agent.get("hp") or 0)
    while not plan.is_complete:
        execute_plan_step(build_agent_context("bot1", agent, state), plan, state, 100 + plan.current_step_index)
    assert int(agent.get("hp") or 0) >= hp_before
