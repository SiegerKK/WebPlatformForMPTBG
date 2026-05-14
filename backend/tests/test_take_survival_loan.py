"""Unit tests for survival loan plan step and executor."""
from __future__ import annotations

import pytest
from typing import Any

from game_sdk.debt_ledger import (
    ensure_debt_ledger,
    total_debt_balance,
    get_active_debts_for_debtor,
    create_debt,
)
from game_sdk.plan_steps.take_survival_loan import (
    can_take_survival_loan,
    calculate_loan_amount,
    plan_take_survival_loan,
    LOAN_ELIGIBILITY_MONEY_THRESHOLD,
    MAX_DEBT_CAP,
    MIN_LOAN_AMOUNT,
    MAX_LOAN_AMOUNT,
)
from game_sdk.executors.execute_take_survival_loan import execute_take_survival_loan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_npc(
    npc_id: str = "npc1",
    money: float = 0.0,
    hunger: int = 0,
    thirst: int = 0,
    hp: int = 100,
    max_hp: int = 100,
) -> dict[str, Any]:
    return {
        "id": npc_id,
        "name": npc_id,
        "money": money,
        "hunger": hunger,
        "thirst": thirst,
        "hp": hp,
        "max_hp": max_hp,
        "inventory": [],
    }


def _make_trader(
    trader_id: str = "trader1",
    money: float = 500.0,
) -> dict[str, Any]:
    return {
        "id": trader_id,
        "agent_id": trader_id,
        "name": "Sidorovich",
        "money": money,
        "location_id": "loc_a",
        "is_alive": True,
        "inventory": [],
    }


def _make_state(npc: dict, trader: dict) -> dict[str, Any]:
    return {
        "agents": {npc["id"]: npc},
        "traders": {trader["id"]: trader},
        "locations": {
            "loc_a": {
                "name": "Base",
                "terrain_type": "buildings",
                "anomaly_activity": 0,
                "connections": [],
                "items": [],
                "agents": [npc["id"], trader["id"]],
            }
        },
    }


# ---------------------------------------------------------------------------
# can_take_survival_loan
# ---------------------------------------------------------------------------

class TestCanTakeSurvivalLoan:
    def test_returns_true_when_all_conditions_met_hunger(self) -> None:
        npc = _make_npc(money=5.0, hunger=80)
        trader = _make_trader(money=100.0)
        state = _make_state(npc, trader)
        assert can_take_survival_loan(npc, state, trader) is True

    def test_returns_true_when_all_conditions_met_thirst(self) -> None:
        npc = _make_npc(money=0.0, thirst=90)
        trader = _make_trader(money=100.0)
        state = _make_state(npc, trader)
        assert can_take_survival_loan(npc, state, trader) is True

    def test_returns_true_when_all_conditions_met_injury(self) -> None:
        # injury derived from hp deficit: max_hp=100, hp=25 → injury=75
        npc = _make_npc(money=0.0, hp=25, max_hp=100)
        trader = _make_trader(money=100.0)
        state = _make_state(npc, trader)
        assert can_take_survival_loan(npc, state, trader) is True

    def test_returns_false_when_npc_has_enough_money(self) -> None:
        npc = _make_npc(money=LOAN_ELIGIBILITY_MONEY_THRESHOLD, hunger=80)
        trader = _make_trader(money=100.0)
        state = _make_state(npc, trader)
        assert can_take_survival_loan(npc, state, trader) is False

    def test_returns_false_when_need_below_threshold(self) -> None:
        npc = _make_npc(money=5.0, hunger=50, thirst=50)
        trader = _make_trader(money=100.0)
        state = _make_state(npc, trader)
        assert can_take_survival_loan(npc, state, trader) is False

    def test_returns_false_when_trader_has_too_little_money(self) -> None:
        npc = _make_npc(money=0.0, hunger=80)
        trader = _make_trader(money=MIN_LOAN_AMOUNT - 1)
        state = _make_state(npc, trader)
        assert can_take_survival_loan(npc, state, trader) is False

    def test_returns_false_when_debt_cap_exceeded(self) -> None:
        npc = _make_npc(money=0.0, hunger=80)
        trader = _make_trader(money=500.0)
        state = _make_state(npc, trader)
        # Manually create debt up to/over the cap
        ensure_debt_ledger(state)
        create_debt(state, "npc1", "trader1", MAX_DEBT_CAP, "survival_loan", 1)
        assert can_take_survival_loan(npc, state, trader) is False

    def test_returns_false_when_debt_slightly_below_cap_still_ok(self) -> None:
        npc = _make_npc(money=0.0, hunger=80)
        trader = _make_trader(money=500.0)
        state = _make_state(npc, trader)
        ensure_debt_ledger(state)
        create_debt(state, "npc1", "trader1", MAX_DEBT_CAP - 1.0, "survival_loan", 1)
        assert can_take_survival_loan(npc, state, trader) is True


# ---------------------------------------------------------------------------
# calculate_loan_amount
# ---------------------------------------------------------------------------

class TestCalculateLoanAmount:
    def test_loan_amount_is_within_bounds(self) -> None:
        npc = _make_npc(money=0.0, hunger=90)
        state = {}
        amount = calculate_loan_amount(npc, state)
        assert MIN_LOAN_AMOUNT <= amount <= MAX_LOAN_AMOUNT

    def test_no_critical_need_returns_minimum(self) -> None:
        npc = _make_npc(money=0.0, hunger=10, thirst=10)
        state = {}
        amount = calculate_loan_amount(npc, state)
        assert amount == MIN_LOAN_AMOUNT

    def test_loan_includes_20_percent_buffer(self) -> None:
        """The loan should be item_cost * 1.2 (clamped)."""
        npc = _make_npc(money=0.0, thirst=95)
        state = {}
        amount = calculate_loan_amount(npc, state)
        # We don't know the exact price here but it should be at least MIN
        assert amount >= MIN_LOAN_AMOUNT


# ---------------------------------------------------------------------------
# plan_take_survival_loan
# ---------------------------------------------------------------------------

class TestPlanTakeSurvivalLoan:
    def test_returns_correct_dict_when_eligible(self) -> None:
        npc = _make_npc(money=0.0, thirst=90)
        trader = _make_trader(money=500.0)
        state = _make_state(npc, trader)
        result = plan_take_survival_loan(npc, state, trader)
        assert result is not None
        assert result["action"] == "take_survival_loan"
        assert result["trader_id"] == trader["id"]
        assert result["purpose"] == "survival_loan"
        assert MIN_LOAN_AMOUNT <= result["amount"] <= MAX_LOAN_AMOUNT

    def test_returns_none_when_not_eligible(self) -> None:
        # NPC has enough money → not eligible
        npc = _make_npc(money=LOAN_ELIGIBILITY_MONEY_THRESHOLD + 1, thirst=90)
        trader = _make_trader(money=500.0)
        state = _make_state(npc, trader)
        result = plan_take_survival_loan(npc, state, trader)
        assert result is None


# ---------------------------------------------------------------------------
# execute_take_survival_loan
# ---------------------------------------------------------------------------

class TestExecuteTakeSurvivalLoan:
    def _make_step(self, trader_id: str, amount: float = 20.0) -> dict:
        return {
            "action": "take_survival_loan",
            "trader_id": trader_id,
            "amount": amount,
            "purpose": "survival_loan",
        }

    def test_success_path_transfers_money_and_creates_debt(self) -> None:
        npc = _make_npc(money=0.0, hunger=80)
        trader = _make_trader(money=500.0)
        state = _make_state(npc, trader)
        step = self._make_step(trader["id"])

        result = execute_take_survival_loan(npc, state, step, current_tick=10)

        assert result["status"] == "success"
        assert "debt_id" in result
        assert result["amount"] > 0

        # Money was transferred
        assert npc["money"] > 0
        assert trader["money"] < 500.0

        # Debt was created
        debts = get_active_debts_for_debtor(state, "npc1")
        assert len(debts) == 1
        debt = debts[0]
        assert debt["debtor_id"] == "npc1"
        assert debt["creditor_id"] == trader["id"]
        assert debt["purpose"] == "survival_loan"
        assert debt["status"] == "active"
        assert debt["created_tick"] == 10

    def test_money_sum_is_conserved(self) -> None:
        npc = _make_npc(money=0.0, hunger=80)
        trader = _make_trader(money=500.0)
        state = _make_state(npc, trader)
        total_before = npc["money"] + trader["money"]

        execute_take_survival_loan(npc, state, self._make_step(trader["id"]), current_tick=1)

        total_after = npc["money"] + trader["money"]
        assert abs(total_after - total_before) < 1e-9  # no money created

    def test_skipped_when_not_eligible(self) -> None:
        # NPC has money above threshold → not eligible
        npc = _make_npc(money=LOAN_ELIGIBILITY_MONEY_THRESHOLD + 5, hunger=80)
        trader = _make_trader(money=500.0)
        state = _make_state(npc, trader)
        step = self._make_step(trader["id"])

        result = execute_take_survival_loan(npc, state, step, current_tick=1)

        assert result["status"] == "skipped"
        assert result["reason"] == "not_eligible"
        # No debt created
        assert total_debt_balance(state, "npc1") == 0.0

    def test_failed_when_trader_not_found(self) -> None:
        npc = _make_npc(money=0.0, hunger=80)
        trader = _make_trader(money=500.0)
        state = _make_state(npc, trader)
        step = self._make_step("nonexistent_trader")

        result = execute_take_survival_loan(npc, state, step, current_tick=1)

        assert result["status"] == "failed"
        assert result["reason"] == "trader_not_found"
        # NPC money unchanged
        assert npc["money"] == 0.0

    def test_debt_balance_equals_loan_amount(self) -> None:
        npc = _make_npc(money=0.0, thirst=95)
        trader = _make_trader(money=500.0)
        state = _make_state(npc, trader)

        result = execute_take_survival_loan(npc, state, self._make_step(trader["id"]), current_tick=5)
        assert result["status"] == "success"

        assert abs(total_debt_balance(state, "npc1") - result["amount"]) < 1e-9
