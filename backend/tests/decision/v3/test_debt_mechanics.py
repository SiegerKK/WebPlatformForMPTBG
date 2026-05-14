from __future__ import annotations

from app.games.zone_stalkers.economy.debts import (
    DEBT_ESCAPE_THRESHOLD,
    SURVIVAL_CREDIT_ROLLOVER_TURNS,
    advance_survival_credit,
    can_request_survival_credit,
    choose_debt_repayment_amount,
    ensure_debt_ledger,
    get_debtor_debt_total,
    repay_debt_account,
    repay_debts_to_creditor_if_useful,
    should_escape_zone_due_to_debt,
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


def test_survival_credit_uses_single_account_per_creditor() -> None:
    state = _state()
    a1 = advance_survival_credit(
        state=state,
        debtor_id="bot1",
        creditor_id="trader_1",
        creditor_type="trader",
        amount=40,
        purpose="survival_drink",
        location_id="loc_a",
        world_turn=100,
    )
    a2 = advance_survival_credit(
        state=state,
        debtor_id="bot1",
        creditor_id="trader_1",
        creditor_type="trader",
        amount=20,
        purpose="survival_food",
        location_id="loc_a",
        world_turn=101,
    )
    assert a1["id"] == a2["id"]
    assert a2["outstanding_total"] == 60


def test_daily_rollover_adds_20_percent_to_remaining_total() -> None:
    state = _state()
    account = advance_survival_credit(
        state=state,
        debtor_id="bot1",
        creditor_id="trader_1",
        creditor_type="trader",
        amount=1000,
        purpose="survival_food",
        location_id="loc_a",
        world_turn=100,
    )
    total = get_debtor_debt_total(state, "bot1", world_turn=100 + SURVIVAL_CREDIT_ROLLOVER_TURNS)
    assert total == 1200
    assert account["rollover_count"] == 1


def test_partial_payment_reduces_next_rollover_base() -> None:
    state = _state()
    debtor = state["agents"]["bot1"]
    creditor = state["traders"]["trader_1"]
    account = advance_survival_credit(
        state=state,
        debtor_id="bot1",
        creditor_id="trader_1",
        creditor_type="trader",
        amount=1000,
        purpose="survival_food",
        location_id="loc_a",
        world_turn=100,
    )
    debtor["money"] = 300
    repay_debt_account(
        state=state,
        debtor=debtor,
        creditor=creditor,
        account_id=account["id"],
        amount=300,
        world_turn=101,
    )
    total = get_debtor_debt_total(state, "bot1", world_turn=100 + SURVIVAL_CREDIT_ROLLOVER_TURNS)
    assert total == 840


def test_survival_credit_has_no_debt_cap_or_default_blocker() -> None:
    state = _state()
    ledger = ensure_debt_ledger(state, world_turn=100)
    ledger["accounts"]["acc_old"] = {
        "id": "acc_old",
        "debtor_id": "bot1",
        "creditor_id": "trader_old",
        "creditor_type": "trader",
        "debtor_type": "agent",
        "outstanding_total": 9000,
        "principal_advanced_total": 9000,
        "repaid_total": 0,
        "rollover_added_total": 0,
        "daily_rollover_rate": 0.2,
        "created_turn": 1,
        "last_advanced_turn": 1,
        "next_due_turn": 200,
        "rollover_count": 0,
        "status": "active",
        "purposes": {},
        "created_location_id": "loc_x",
        "source": "trader_survival_credit",
        "notes": {},
    }
    ok, reason = can_request_survival_credit(
        state=state,
        debtor=state["agents"]["bot1"],
        creditor=state["traders"]["trader_1"],
        creditor_type="trader",
        item_category="drink",
        required_price=45,
        world_turn=130,
    )
    assert ok is True
    assert reason == "ok"


def test_debt_escape_threshold_at_5000() -> None:
    state = _state()
    advance_survival_credit(
        state=state,
        debtor_id="bot1",
        creditor_id="trader_1",
        creditor_type="trader",
        amount=DEBT_ESCAPE_THRESHOLD,
        purpose="survival_food",
        location_id="loc_a",
        world_turn=100,
    )
    assert should_escape_zone_due_to_debt(state, "bot1", world_turn=100) is True


def test_partial_repayment_emits_debt_payment_not_debt_repaid() -> None:
    state = _state()
    debtor = state["agents"]["bot1"]
    creditor = state["traders"]["trader_1"]
    advance_survival_credit(
        state=state,
        debtor_id="bot1",
        creditor_id="trader_1",
        creditor_type="trader",
        amount=600,
        purpose="survival_food",
        location_id="loc_a",
        world_turn=100,
    )
    debtor["money"] = 200
    events = repay_debts_to_creditor_if_useful(
        state=state,
        debtor=debtor,
        creditor=creditor,
        world_turn=120,
    )
    kinds = {ev["event_type"] for ev in events}
    assert "debt_payment" in kinds
    assert "debt_repaid" not in kinds


def test_near_due_repayment_pays_more_aggressively() -> None:
    state = _state()
    debtor = state["agents"]["bot1"]
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
    debtor["money"] = 300
    normal = choose_debt_repayment_amount(
        debtor=debtor,
        account={**account, "next_due_turn": 1000},
        world_turn=100,
        critical_needs=False,
    )
    urgent = choose_debt_repayment_amount(
        debtor=debtor,
        account={**account, "next_due_turn": 150},
        world_turn=100,
        critical_needs=False,
    )
    assert urgent >= normal
