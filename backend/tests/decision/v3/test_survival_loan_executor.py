from __future__ import annotations

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.executors import _trade_buy_succeeded, execute_plan_step
from app.games.zone_stalkers.decision.models.plan import (
    Plan,
    PlanStep,
    STEP_CONSUME_ITEM,
    STEP_REQUEST_LOAN,
    STEP_TRADE_BUY_ITEM,
)
from app.games.zone_stalkers.decision.active_plan_manager import create_active_plan, get_active_plan, save_active_plan
from app.games.zone_stalkers.decision.active_plan_runtime import process_active_plan_v3, start_or_continue_active_plan_step
from app.games.zone_stalkers.decision.models.objective import Objective, ObjectiveDecision, ObjectiveScore
from app.games.zone_stalkers.balance.items import ITEM_TYPES
from app.games.zone_stalkers.economy.debts import advance_survival_credit
from tests.decision.conftest import make_agent, make_state_with_trader


def _state(agent: dict) -> dict:
    state = make_state_with_trader(agent=agent, trader_at="loc_a")
    trader = state["traders"]["trader_1"]
    trader["id"] = "trader_1"
    trader["money"] = 0
    trader["accounts_receivable"] = 0
    return state


def _run_plan_steps(agent: dict, state: dict, plan: Plan, *, max_steps: int = 10) -> None:
    for _ in range(max_steps):
        if plan.is_complete:
            return
        execute_plan_step(
            build_agent_context("bot1", agent, state),
            plan,
            state,
            100 + plan.current_step_index,
        )


def _run_plan_until_complete(agent: dict, state: dict, plan: Plan, *, max_steps: int = 10) -> None:
    _run_plan_steps(agent, state, plan, max_steps=max_steps)
    if plan.is_complete:
        return
    current_step = plan.steps[plan.current_step_index] if plan.current_step_index < len(plan.steps) else None
    raise AssertionError({
        "reason": "plan did not complete",
        "current_step_index": plan.current_step_index,
        "current_step": current_step.kind if current_step else None,
        "step_payload": current_step.payload if current_step else None,
        "agent_money": agent.get("money"),
        "inventory": agent.get("inventory"),
    })


def _debt_credit_events(events: list[dict[str, object]]) -> list[dict[str, object]]:
    return [ev for ev in events if isinstance(ev, dict) and ev.get("event_type") == "debt_credit_advanced"]


def test_request_loan_autocorrects_too_small_medical_amount_to_required_price() -> None:
    agent = make_agent(money=0, hp=40, inventory=[], has_weapon=False, has_armor=False, has_ammo=False)
    state = _state(agent)
    bandage_price = int(ITEM_TYPES["bandage"]["value"] * 1.5)
    plan = Plan(
        intent_kind="heal_self",
        steps=[PlanStep(
            kind=STEP_REQUEST_LOAN,
            payload={
                "creditor_id": "trader_1",
                "creditor_type": "trader",
                "amount": 10,
                "purpose": "survival_medical",
                "item_category": "medical",
                "required_price": bandage_price,
                "daily_interest_rate": 0.05,
            },
            interruptible=False,
        )],
    )

    events = execute_plan_step(build_agent_context("bot1", agent, state), plan, state, 100)

    credit_events = _debt_credit_events(events)
    assert len(credit_events) == 1
    assert credit_events[0]["payload"]["amount"] == bandage_price
    assert credit_events[0]["payload"]["principal_needed"] == bandage_price
    assert credit_events[0]["payload"]["required_price"] == bandage_price
    assert credit_events[0]["payload"]["expected_item_type"] == ""
    assert int(agent.get("money") or 0) == bandage_price
    assert plan.steps[0].payload["amount"] == bandage_price
    assert plan.steps[0].payload["amount_corrected_to_required_price"] is True


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
    assert any(ev.get("event_type") == "debt_credit_advanced" for ev in events)
    assert int(agent.get("money") or 0) == 45
    assert int(state["traders"]["trader_1"].get("accounts_receivable") or 0) == 35
    assert int(state["traders"]["trader_1"].get("money") or 0) == 0
    assert state.get("debt_ledger", {}).get("accounts")


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
    assert any(ev.get("event_type") == "debt_credit_advanced" for ev in events)
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
                    "purpose": "survival_weapon",
                    "item_category": "weapon",
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
    _run_plan_until_complete(agent, state, plan)
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
    _run_plan_until_complete(agent, state, plan)
    assert int(agent.get("hunger") or 0) < hunger_before


def test_request_loan_then_trade_buy_then_consume_medical() -> None:
    agent = make_agent(money=0, hp=40, inventory=[], has_weapon=False, has_armor=False, has_ammo=False)
    state = _state(agent)
    bandage_price = int(ITEM_TYPES["bandage"]["value"] * 1.5)
    plan = Plan(
        intent_kind="heal_self",
        steps=[
            PlanStep(
                kind=STEP_REQUEST_LOAN,
                payload={
                    "creditor_id": "trader_1",
                    "creditor_type": "trader",
                    "amount": bandage_price,
                    "purpose": "survival_medical",
                    "item_category": "medical",
                    "required_price": bandage_price,
                    "daily_interest_rate": 0.05,
                },
                interruptible=False,
            ),
            PlanStep(kind=STEP_TRADE_BUY_ITEM, payload={"item_category": "medical", "buy_mode": "survival_cheapest", "reason": "buy_medical_survival"}, interruptible=False),
            PlanStep(kind=STEP_CONSUME_ITEM, payload={"item_type": "bandage", "reason": "emergency_heal"}, interruptible=False),
        ],
        current_step_index=0,
    )
    hp_before = int(agent.get("hp") or 0)
    _run_plan_until_complete(agent, state, plan)
    assert int(agent.get("hp") or 0) >= hp_before


def test_request_loan_partial_money_requests_only_missing_amount() -> None:
    agent = make_agent(money=25, hp=40, inventory=[], has_weapon=False, has_armor=False, has_ammo=False)
    state = _state(agent)
    bandage_price = int(ITEM_TYPES["bandage"]["value"] * 1.5)
    plan = Plan(
        intent_kind="heal_self",
        steps=[PlanStep(
            kind=STEP_REQUEST_LOAN,
            payload={
                "creditor_id": "trader_1",
                "creditor_type": "trader",
                "amount": 10,
                "purpose": "survival_medical",
                "item_category": "medical",
                "required_price": bandage_price,
                "daily_interest_rate": 0.05,
            },
            interruptible=False,
        )],
    )

    events = execute_plan_step(build_agent_context("bot1", agent, state), plan, state, 100)

    credit_events = _debt_credit_events(events)
    assert len(credit_events) == 1
    assert credit_events[0]["payload"]["amount"] == bandage_price - 25
    assert int(agent.get("money") or 0) == bandage_price



def test_request_loan_then_trade_buy_then_consume_medical_no_microcredit_loop() -> None:
    agent = make_agent(money=0, hp=40, inventory=[], has_weapon=False, has_armor=False, has_ammo=False)
    state = _state(agent)
    bandage_price = int(ITEM_TYPES["bandage"]["value"] * 1.5)
    plan = Plan(
        intent_kind="heal_self",
        steps=[
            PlanStep(
                kind=STEP_REQUEST_LOAN,
                payload={
                    "creditor_id": "trader_1",
                    "creditor_type": "trader",
                    "amount": 10,
                    "purpose": "survival_medical",
                    "item_category": "medical",
                    "required_price": bandage_price,
                    "survival_credit_quote_item_type": "bandage",
                    "daily_interest_rate": 0.05,
                },
                interruptible=False,
            ),
            PlanStep(
                kind=STEP_TRADE_BUY_ITEM,
                payload={
                    "item_category": "medical",
                    "buy_mode": "survival_cheapest",
                    "reason": "buy_medical_survival",
                    "compatible_item_types": ["bandage"],
                    "required_price": bandage_price,
                    "expected_item_type": "bandage",
                    "previous_step_was_survival_credit": True,
                },
                interruptible=False,
            ),
            PlanStep(kind=STEP_CONSUME_ITEM, payload={"item_type": "bandage", "reason": "emergency_heal"}, interruptible=False),
        ],
        current_step_index=0,
    )

    hp_before = int(agent.get("hp") or 0)
    collected_events: list[dict[str, object]] = []
    for step_offset in range(5):
        if plan.is_complete:
            break
        collected_events.extend(
            execute_plan_step(build_agent_context("bot1", agent, state), plan, state, 100 + step_offset)
        )

    assert plan.is_complete
    debt_events = _debt_credit_events(collected_events)
    assert len(debt_events) == 1
    assert debt_events[0]["payload"]["amount"] == bandage_price
    assert _trade_buy_succeeded(collected_events) is True
    assert int(agent.get("hp") or 0) >= hp_before
    assert all(ev.get("event_type") != "trade_buy_failed" for ev in collected_events)



def test_tiny_medical_loan_payload_does_not_create_tiny_credit() -> None:
    agent = make_agent(money=0, hp=40, inventory=[], has_weapon=False, has_armor=False, has_ammo=False)
    state = _state(agent)
    bandage_price = int(ITEM_TYPES["bandage"]["value"] * 1.5)
    plan = Plan(
        intent_kind="heal_self",
        steps=[PlanStep(
            kind=STEP_REQUEST_LOAN,
            payload={
                "creditor_id": "trader_1",
                "creditor_type": "trader",
                "amount": 10,
                "purpose": "survival_medical",
                "item_category": "medical",
                "required_price": bandage_price,
                "daily_interest_rate": 0.05,
            },
            interruptible=False,
        )],
    )

    events = execute_plan_step(build_agent_context("bot1", agent, state), plan, state, 100)

    credit_events = _debt_credit_events(events)
    assert len(credit_events) == 1
    assert credit_events[0]["payload"]["amount"] == bandage_price
    assert credit_events[0]["payload"]["amount"] != 10



def test_active_plan_request_loan_failure_marks_step_failed_and_aborts_or_replans() -> None:
    agent = make_agent(money=0, thirst=100, inventory=[], has_weapon=False, has_armor=False, has_ammo=False)
    state = _state(agent)
    objective = Objective(
        key="RESTORE_WATER",
        source="test",
        urgency=1.0,
        expected_value=1.0,
        risk=0.1,
        time_cost=0.1,
        resource_cost=0.1,
        confidence=1.0,
        goal_alignment=1.0,
        memory_confidence=1.0,
    )
    decision = ObjectiveDecision(
        selected=objective,
        selected_score=ObjectiveScore(
            objective_key="RESTORE_WATER",
            raw_score=1.0,
            final_score=1.0,
            factors=(),
            penalties=(),
        ),
        alternatives=(),
    )
    active_plan = create_active_plan(
        decision,
        world_turn=100,
        plan=Plan(
            intent_kind="seek_water",
            steps=[
                PlanStep(
                    kind=STEP_REQUEST_LOAN,
                    payload={
                        "creditor_id": "trader_1",
                        "creditor_type": "trader",
                        "amount": 1000,  # impossible, should fail
                        "purpose": "survival_weapon",
                        "item_category": "weapon",
                        "required_price": 1000,
                        "daily_interest_rate": 0.05,
                    },
                    interruptible=False,
                ),
                PlanStep(kind=STEP_TRADE_BUY_ITEM, payload={"item_category": "drink"}, interruptible=False),
            ],
        ),
    )
    save_active_plan(agent, active_plan)

    events = start_or_continue_active_plan_step(
        "bot1",
        agent,
        active_plan,
        state,
        100,
        add_memory=lambda *args, **kwargs: None,
    )
    assert all((ev.get("event_type") or "") != "bot_bought_item" for ev in events if isinstance(ev, dict))
    failed_plan = get_active_plan(agent)
    assert failed_plan is not None
    assert failed_plan.current_step is not None
    assert failed_plan.current_step.kind == STEP_REQUEST_LOAN
    assert failed_plan.current_step.status == "failed"
    assert str(failed_plan.current_step.failure_reason or "").startswith("request_loan_failed:")

    handled, _ = process_active_plan_v3(
        "bot1",
        agent,
        state,
        101,
        add_memory=lambda *args, **kwargs: None,
    )
    assert handled is False
    invalidators = ((agent.get("brain_runtime") or {}).get("invalidators") or [])
    assert any(
        isinstance(inv, dict) and str(inv.get("reason") or "") == "request_loan_failed"
        for inv in invalidators
    )


def test_debtor_with_enough_money_repays_on_trader_interaction_agent39_regression() -> None:
    agent = make_agent(money=47435, thirst=20, hunger=20, inventory=[], has_weapon=False, has_armor=False, has_ammo=False)
    state = _state(agent)
    trader = state["traders"]["trader_1"]
    trader["inventory"] = [{"id": "w1", "type": "water", "name": "Water", "value": 30}]

    account = advance_survival_credit(
        state=state,
        debtor_id="bot1",
        creditor_id="trader_1",
        creditor_type="trader",
        amount=600,
        purpose="survival_food",
        location_id="loc_a",
        world_turn=100,
    )

    before_money = int(agent.get("money") or 0)
    plan = Plan(
        intent_kind="seek_water",
        steps=[PlanStep(kind=STEP_TRADE_BUY_ITEM, payload={"item_category": "drink", "reason": "buy_drink_survival", "buy_mode": "survival_cheapest"}, interruptible=False)],
    )
    events = execute_plan_step(build_agent_context("bot1", agent, state), plan, state, 101)

    event_types = [str((ev or {}).get("event_type") or "") for ev in events if isinstance(ev, dict)]
    assert "debt_payment" in event_types
    assert "debt_repaid" in event_types
    assert int(account.get("outstanding_total") or 0) == 0
    assert str(account.get("status") or "") == "repaid"
    assert int(agent.get("money") or 0) < before_money
