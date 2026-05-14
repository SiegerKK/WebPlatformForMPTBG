"""Unit tests for game_sdk.debt_ledger."""
from __future__ import annotations

import pytest

from game_sdk.debt_ledger import (
    apply_repayment,
    create_debt,
    default_overdue_debts,
    ensure_debt_ledger,
    get_active_debts_for_debtor,
    get_debts_for_creditor,
    get_debts_for_debtor,
    total_debt_balance,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _empty_state() -> dict:
    return {}


def _state_with_ledger() -> dict:
    state = _empty_state()
    ensure_debt_ledger(state)
    return state


# ---------------------------------------------------------------------------
# ensure_debt_ledger
# ---------------------------------------------------------------------------

def test_ensure_debt_ledger_initialises_on_empty_state() -> None:
    state = _empty_state()
    ensure_debt_ledger(state)
    assert "debt_ledger" in state
    ledger = state["debt_ledger"]
    assert ledger["version"] == 1
    assert isinstance(ledger["debts"], dict)
    assert isinstance(ledger["by_debtor"], dict)
    assert isinstance(ledger["by_creditor"], dict)


def test_ensure_debt_ledger_is_idempotent() -> None:
    state = _empty_state()
    ensure_debt_ledger(state)
    ensure_debt_ledger(state)  # second call must not clear or raise
    assert "debt_ledger" in state
    assert state["debt_ledger"]["version"] == 1


def test_ensure_debt_ledger_does_not_overwrite_existing() -> None:
    state = _empty_state()
    ensure_debt_ledger(state)
    # Manually add a debt
    create_debt(state, "npc1", "trader1", 20.0, "survival_loan", 10)
    ensure_debt_ledger(state)  # must NOT reset
    assert len(state["debt_ledger"]["debts"]) == 1


# ---------------------------------------------------------------------------
# create_debt
# ---------------------------------------------------------------------------

def test_create_debt_returns_contract_with_expected_fields() -> None:
    state = _empty_state()
    debt = create_debt(state, "npc1", "trader1", 30.0, "survival_loan", 42)
    assert debt["debtor_id"] == "npc1"
    assert debt["creditor_id"] == "trader1"
    assert debt["principal"] == 30.0
    assert debt["balance"] == 30.0
    assert debt["interest_rate"] == 0.0
    assert debt["status"] == "active"
    assert debt["created_tick"] == 42
    assert debt["due_tick"] is None
    assert debt["purpose"] == "survival_loan"
    assert debt["repayment_history"] == []
    assert debt["id"].startswith("debt_")


def test_create_debt_with_due_tick_and_metadata() -> None:
    state = _empty_state()
    debt = create_debt(
        state, "npc2", "trader2", 10.0, "trade_credit", 5,
        due_tick=100, metadata={"reason": "test"},
    )
    assert debt["due_tick"] == 100
    assert debt["metadata"]["reason"] == "test"


def test_create_debt_inserts_into_ledger_indexes() -> None:
    state = _empty_state()
    debt = create_debt(state, "npc1", "trader1", 10.0, "survival_loan", 1)
    ledger = state["debt_ledger"]
    assert debt["id"] in ledger["debts"]
    assert debt["id"] in ledger["by_debtor"]["npc1"]
    assert debt["id"] in ledger["by_creditor"]["trader1"]


def test_create_multiple_debts_all_indexed() -> None:
    state = _empty_state()
    d1 = create_debt(state, "npc1", "trader1", 10.0, "survival_loan", 1)
    d2 = create_debt(state, "npc1", "trader1", 20.0, "survival_loan", 2)
    d3 = create_debt(state, "npc2", "trader1", 5.0, "survival_loan", 3)

    assert len(state["debt_ledger"]["by_debtor"]["npc1"]) == 2
    assert d1["id"] in state["debt_ledger"]["by_debtor"]["npc1"]
    assert d2["id"] in state["debt_ledger"]["by_debtor"]["npc1"]
    assert len(state["debt_ledger"]["by_debtor"].get("npc2", [])) == 1
    assert len(state["debt_ledger"]["by_creditor"]["trader1"]) == 3


# ---------------------------------------------------------------------------
# get_debts_for_debtor / get_debts_for_creditor
# ---------------------------------------------------------------------------

def test_get_debts_for_debtor_returns_all_statuses() -> None:
    state = _empty_state()
    d1 = create_debt(state, "npc1", "trader1", 10.0, "survival_loan", 1)
    d2 = create_debt(state, "npc1", "trader2", 5.0, "survival_loan", 2)
    create_debt(state, "npc2", "trader1", 7.0, "survival_loan", 3)

    debts = get_debts_for_debtor(state, "npc1")
    ids = {d["id"] for d in debts}
    assert d1["id"] in ids
    assert d2["id"] in ids
    assert len(debts) == 2


def test_get_debts_for_debtor_empty_when_no_debts() -> None:
    state = _empty_state()
    assert get_debts_for_debtor(state, "nobody") == []


def test_get_debts_for_creditor_returns_matching_debts() -> None:
    state = _empty_state()
    d1 = create_debt(state, "npc1", "trader1", 10.0, "survival_loan", 1)
    d2 = create_debt(state, "npc2", "trader1", 5.0, "survival_loan", 2)
    create_debt(state, "npc1", "trader2", 20.0, "survival_loan", 3)

    debts = get_debts_for_creditor(state, "trader1")
    ids = {d["id"] for d in debts}
    assert d1["id"] in ids
    assert d2["id"] in ids
    assert len(debts) == 2


# ---------------------------------------------------------------------------
# get_active_debts_for_debtor
# ---------------------------------------------------------------------------

def test_get_active_debts_for_debtor_filters_repaid() -> None:
    state = _empty_state()
    d1 = create_debt(state, "npc1", "trader1", 10.0, "survival_loan", 1)
    d2 = create_debt(state, "npc1", "trader1", 20.0, "survival_loan", 2)
    # Repay d1 fully
    apply_repayment(state, d1["id"], 10.0, 5)
    assert state["debt_ledger"]["debts"][d1["id"]]["status"] == "repaid"

    active = get_active_debts_for_debtor(state, "npc1")
    assert len(active) == 1
    assert active[0]["id"] == d2["id"]


def test_get_active_debts_for_debtor_returns_oldest_first() -> None:
    state = _empty_state()
    d1 = create_debt(state, "npc1", "trader1", 10.0, "survival_loan", created_tick=1)
    d2 = create_debt(state, "npc1", "trader1", 10.0, "survival_loan", created_tick=10)
    d3 = create_debt(state, "npc1", "trader1", 10.0, "survival_loan", created_tick=5)

    active = get_active_debts_for_debtor(state, "npc1")
    ticks = [d["created_tick"] for d in active]
    assert ticks == sorted(ticks)


# ---------------------------------------------------------------------------
# total_debt_balance
# ---------------------------------------------------------------------------

def test_total_debt_balance_sums_active_balances() -> None:
    state = _empty_state()
    create_debt(state, "npc1", "trader1", 15.0, "survival_loan", 1)
    create_debt(state, "npc1", "trader1", 25.0, "survival_loan", 2)
    assert total_debt_balance(state, "npc1") == 40.0


def test_total_debt_balance_excludes_repaid() -> None:
    state = _empty_state()
    d1 = create_debt(state, "npc1", "trader1", 10.0, "survival_loan", 1)
    create_debt(state, "npc1", "trader1", 20.0, "survival_loan", 2)
    apply_repayment(state, d1["id"], 10.0, 5)
    assert total_debt_balance(state, "npc1") == 20.0


def test_total_debt_balance_zero_for_unknown_debtor() -> None:
    state = _empty_state()
    assert total_debt_balance(state, "ghost") == 0.0


# ---------------------------------------------------------------------------
# apply_repayment
# ---------------------------------------------------------------------------

def test_apply_repayment_reduces_balance() -> None:
    state = _empty_state()
    debt = create_debt(state, "npc1", "trader1", 30.0, "survival_loan", 1)
    new_balance = apply_repayment(state, debt["id"], 10.0, 5)
    assert new_balance == 20.0
    assert state["debt_ledger"]["debts"][debt["id"]]["balance"] == 20.0


def test_apply_repayment_appends_history() -> None:
    state = _empty_state()
    debt = create_debt(state, "npc1", "trader1", 30.0, "survival_loan", 1)
    apply_repayment(state, debt["id"], 10.0, 5)
    apply_repayment(state, debt["id"], 5.0, 10)
    history = state["debt_ledger"]["debts"][debt["id"]]["repayment_history"]
    assert len(history) == 2
    assert history[0] == {"tick": 5, "amount": 10.0}
    assert history[1] == {"tick": 10, "amount": 5.0}


def test_apply_repayment_marks_repaid_when_balance_reaches_zero() -> None:
    state = _empty_state()
    debt = create_debt(state, "npc1", "trader1", 20.0, "survival_loan", 1)
    apply_repayment(state, debt["id"], 20.0, 3)
    assert state["debt_ledger"]["debts"][debt["id"]]["status"] == "repaid"


def test_apply_repayment_marks_repaid_on_overpayment() -> None:
    state = _empty_state()
    debt = create_debt(state, "npc1", "trader1", 20.0, "survival_loan", 1)
    new_balance = apply_repayment(state, debt["id"], 999.0, 3)
    assert new_balance == 0.0
    assert state["debt_ledger"]["debts"][debt["id"]]["status"] == "repaid"


def test_apply_repayment_returns_zero_for_unknown_debt() -> None:
    state = _empty_state()
    result = apply_repayment(state, "debt_doesnotexist", 10.0, 1)
    assert result == 0.0


# ---------------------------------------------------------------------------
# default_overdue_debts (stub — should not raise)
# ---------------------------------------------------------------------------

def test_default_overdue_debts_stub_does_not_raise() -> None:
    state = _empty_state()
    create_debt(state, "npc1", "trader1", 10.0, "survival_loan", 1, due_tick=5)
    # Should not raise; body is pass
    default_overdue_debts(state, current_tick=100)
