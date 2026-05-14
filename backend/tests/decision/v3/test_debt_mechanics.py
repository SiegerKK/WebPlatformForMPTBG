from __future__ import annotations

from app.games.zone_stalkers.economy.debts import (
    SURVIVAL_LOAN_DAILY_INTEREST_RATE,
    SURVIVAL_LOAN_DUE_TURNS,
    SURVIVAL_LOAN_REPAYMENT_RESERVE_MONEY,
    auto_repay_debts,
    can_request_survival_loan,
    create_debt,
    ensure_debt_ledger,
    get_debtor_outstanding_total,
    mark_overdue_debts,
    repay_debt,
)


def _state() -> dict:
    return {
        "world_turn": 100,
        "agents": {
            "bot1": {"id": "bot1", "is_alive": True, "money": 0},
        },
        "traders": {
            "trader_1": {"id": "trader_1", "is_alive": True, "money": 1000, "accounts_receivable": 0},
        },
    }


def test_create_survival_debt_contract_schema() -> None:
    state = _state()
    debt = create_debt(
        state=state,
        debtor_id="bot1",
        creditor_id="trader_1",
        creditor_type="trader",
        principal=45,
        purpose="survival_drink",
        allowed_item_category="drink",
        location_id="loc_a",
        daily_interest_rate=SURVIVAL_LOAN_DAILY_INTEREST_RATE,
        due_turn=100 + SURVIVAL_LOAN_DUE_TURNS,
        world_turn=100,
    )
    assert debt["outstanding_principal"] == 45
    assert debt["accrued_interest"] == 0.0
    assert debt["status"] == "active"
    assert debt["creditor_type"] == "trader"
    assert debt["allowed_item_category"] == "drink"
    assert debt["id"].startswith("debt_")


def test_interest_accrues_after_one_day_without_compounding() -> None:
    state = _state()
    debt = create_debt(
        state=state,
        debtor_id="bot1",
        creditor_id="trader_1",
        creditor_type="trader",
        principal=100,
        purpose="survival_food",
        allowed_item_category="food",
        location_id="loc_a",
        daily_interest_rate=0.05,
        due_turn=2000,
        world_turn=100,
    )
    total = get_debtor_outstanding_total(state, "bot1", world_turn=1540)
    assert total == 105
    assert debt["accrued_interest"] == 5.0


def test_partial_repayment_pays_interest_first() -> None:
    state = _state()
    debtor = state["agents"]["bot1"]
    creditor = state["traders"]["trader_1"]
    debt = create_debt(
        state=state,
        debtor_id="bot1",
        creditor_id="trader_1",
        creditor_type="trader",
        principal=100,
        purpose="survival_food",
        allowed_item_category="food",
        location_id="loc_a",
        daily_interest_rate=0.05,
        due_turn=2000,
        world_turn=100,
    )
    get_debtor_outstanding_total(state, "bot1", world_turn=1540)
    debtor["money"] = 3
    result = repay_debt(
        state=state,
        debtor=debtor,
        creditor=creditor,
        debt_id=debt["id"],
        amount=3,
        world_turn=1540,
    )
    assert result["paid"] == 3
    assert debt["accrued_interest"] == 2.0
    assert debt["outstanding_principal"] == 100


def test_full_repayment_marks_debt_repaid() -> None:
    state = _state()
    debtor = state["agents"]["bot1"]
    creditor = state["traders"]["trader_1"]
    debt = create_debt(
        state=state,
        debtor_id="bot1",
        creditor_id="trader_1",
        creditor_type="trader",
        principal=20,
        purpose="survival_drink",
        allowed_item_category="drink",
        location_id="loc_a",
        daily_interest_rate=0.0,
        due_turn=2000,
        world_turn=100,
    )
    debtor["money"] = 20
    repay_debt(
        state=state,
        debtor=debtor,
        creditor=creditor,
        debt_id=debt["id"],
        amount=20,
        world_turn=101,
    )
    assert debt["status"] == "repaid"
    assert debt["outstanding_principal"] == 0.0
    assert debt["accrued_interest"] == 0.0


def test_debt_becomes_overdue_after_due_turn() -> None:
    state = _state()
    debt = create_debt(
        state=state,
        debtor_id="bot1",
        creditor_id="trader_1",
        creditor_type="trader",
        principal=20,
        purpose="survival_drink",
        allowed_item_category="drink",
        location_id="loc_a",
        daily_interest_rate=0.0,
        due_turn=120,
        world_turn=100,
    )
    changed = mark_overdue_debts(state, world_turn=121)
    assert changed == 1
    assert debt["status"] == "overdue"


def test_defaulted_debt_blocks_new_survival_credit() -> None:
    state = _state()
    debt = create_debt(
        state=state,
        debtor_id="bot1",
        creditor_id="trader_1",
        creditor_type="trader",
        principal=20,
        purpose="survival_drink",
        allowed_item_category="drink",
        location_id="loc_a",
        daily_interest_rate=0.0,
        due_turn=120,
        world_turn=100,
    )
    debt["status"] = "defaulted"
    ok, reason = can_request_survival_loan(
        state=state,
        debtor=state["agents"]["bot1"],
        creditor=state["traders"]["trader_1"],
        creditor_type="trader",
        item_category="drink",
        required_price=50,
        world_turn=130,
    )
    assert ok is False
    assert reason == "has_defaulted_debt"


def test_active_debt_cap_blocks_infinite_loans() -> None:
    state = _state()
    ensure_debt_ledger(state)
    for idx in range(3):
        create_debt(
            state=state,
            debtor_id="bot1",
            creditor_id=f"trader_{idx}",
            creditor_type="trader",
            principal=200,
            purpose="survival_drink",
            allowed_item_category="drink",
            location_id="loc_a",
            daily_interest_rate=0.0,
            due_turn=2000,
            world_turn=100,
        )
    ok, reason = can_request_survival_loan(
        state=state,
        debtor=state["agents"]["bot1"],
        creditor=state["traders"]["trader_1"],
        creditor_type="trader",
        item_category="drink",
        required_price=120,
        world_turn=101,
    )
    assert ok is False
    assert reason == "debt_limit_reached"


def test_can_request_survival_loan_uses_agent_id_fallback() -> None:
    state = _state()
    debtor = {"agent_id": "bot1", "is_alive": True, "money": 0}
    ok, reason = can_request_survival_loan(
        state=state,
        debtor=debtor,
        creditor=state["traders"]["trader_1"],
        creditor_type="trader",
        item_category="drink",
        required_price=45,
        world_turn=100,
    )
    assert ok is True
    assert reason == "ok"


def test_successful_artifact_sale_auto_repays_debt_interest_first() -> None:
    state = _state()
    debtor = state["agents"]["bot1"]
    creditor = state["traders"]["trader_1"]
    debt = create_debt(
        state=state,
        debtor_id="bot1",
        creditor_id="trader_1",
        creditor_type="trader",
        principal=100,
        purpose="survival_food",
        allowed_item_category="food",
        location_id="loc_a",
        daily_interest_rate=0.05,
        due_turn=3000,
        world_turn=100,
    )
    get_debtor_outstanding_total(state, "bot1", world_turn=1540)  # +5 interest
    debtor["money"] = 150
    events = auto_repay_debts(
        state=state,
        debtor=debtor,
        creditor=creditor,
        world_turn=1540,
    )
    assert events
    assert debt["accrued_interest"] == 0.0
    assert debt["outstanding_principal"] <= 55


def test_auto_repay_never_reduces_agent_below_reserve() -> None:
    state = _state()
    debtor = state["agents"]["bot1"]
    creditor = state["traders"]["trader_1"]
    create_debt(
        state=state,
        debtor_id="bot1",
        creditor_id="trader_1",
        creditor_type="trader",
        principal=200,
        purpose="survival_food",
        allowed_item_category="food",
        location_id="loc_a",
        daily_interest_rate=0.0,
        due_turn=3000,
        world_turn=100,
    )
    debtor["money"] = SURVIVAL_LOAN_REPAYMENT_RESERVE_MONEY + 20
    auto_repay_debts(state=state, debtor=debtor, creditor=creditor, world_turn=200)
    assert int(debtor["money"]) >= SURVIVAL_LOAN_REPAYMENT_RESERVE_MONEY
