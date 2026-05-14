"""take_survival_loan — plan-step helpers for NPC survival loans.

An NPC with a critical survival need (hunger / thirst / injury ≥ 70) who
cannot afford a survival item and has no safe inventory to sell may take a
small loan from a co-located trader.

Public API
----------
can_take_survival_loan(npc, state, trader_npc) -> bool
calculate_loan_amount(npc, state) -> float
plan_take_survival_loan(npc, state, trader_npc) -> dict | None
"""
from __future__ import annotations

from typing import Any, Optional

from game_sdk.debt_ledger import total_debt_balance

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOAN_ELIGIBILITY_MONEY_THRESHOLD: float = 15.0
MIN_LOAN_AMOUNT: float = 5.0
MAX_LOAN_AMOUNT: float = 50.0
MAX_DEBT_CAP: float = 100.0
TRADER_LEND_WILLINGNESS: bool = True  # stub — always True for now

# Critical need threshold (0-100 scale)
_CRITICAL_NEED_THRESHOLD: int = 70


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------

def can_take_survival_loan(
    npc: dict[str, Any],
    state: dict[str, Any],
    trader_npc: dict[str, Any],
) -> bool:
    """Return True if the NPC is eligible to receive a survival loan.

    Conditions (ALL must be true):
    1. NPC has a critical survival need: hunger, thirst, or injury ≥ 70.
    2. NPC money < LOAN_ELIGIBILITY_MONEY_THRESHOLD.
    3. Trader has money ≥ MIN_LOAN_AMOUNT.
    4. NPC's total active debt balance < MAX_DEBT_CAP.
    5. Trader is willing to lend (TRADER_LEND_WILLINGNESS stub).
    """
    # 1. Critical survival need
    hunger = int(npc.get("hunger") or 0)
    thirst = int(npc.get("thirst") or 0)
    # injury: derive from hp deficit if no explicit field
    hp = int(npc.get("hp") or 100)
    max_hp = int(npc.get("max_hp") or 100)
    injury = int(npc.get("injury") or 0) or max(0, max_hp - hp)
    has_critical_need = (
        hunger >= _CRITICAL_NEED_THRESHOLD
        or thirst >= _CRITICAL_NEED_THRESHOLD
        or injury >= _CRITICAL_NEED_THRESHOLD
    )
    if not has_critical_need:
        return False

    # 2. NPC low on money
    npc_money = float(npc.get("money") or 0)
    if npc_money >= LOAN_ELIGIBILITY_MONEY_THRESHOLD:
        return False

    # 3. Trader has enough money
    trader_money = float(trader_npc.get("money") or 0)
    if trader_money < MIN_LOAN_AMOUNT:
        return False

    # 4. Debt cap
    npc_id = str(npc.get("id") or npc.get("agent_id") or "")
    if npc_id and total_debt_balance(state, npc_id) >= MAX_DEBT_CAP:
        return False

    # 5. Trader willingness (stub)
    if not TRADER_LEND_WILLINGNESS:
        return False

    return True


# ---------------------------------------------------------------------------
# Loan amount calculation
# ---------------------------------------------------------------------------

def calculate_loan_amount(
    npc: dict[str, Any],
    state: dict[str, Any],
) -> float:
    """Compute the loan amount needed to buy the cheapest survival item.

    Uses a 20% buffer on top of the item cost, clamped to
    [MIN_LOAN_AMOUNT, MAX_LOAN_AMOUNT].  Falls back to MIN_LOAN_AMOUNT if no
    relevant item can be found.
    """
    try:
        from app.games.zone_stalkers.balance.items import (
            ITEM_TYPES,
            FOOD_ITEM_TYPES,
            DRINK_ITEM_TYPES,
            HEAL_ITEM_TYPES,
        )
    except ImportError:
        return MIN_LOAN_AMOUNT

    hunger = int(npc.get("hunger") or 0)
    thirst = int(npc.get("thirst") or 0)
    hp = int(npc.get("hp") or 100)
    max_hp = int(npc.get("max_hp") or 100)
    injury = int(npc.get("injury") or 0) or max(0, max_hp - hp)

    # Determine most critical need to prioritise cheapest item category.
    need_scores: list[tuple[int, frozenset[str]]] = []
    if hunger >= _CRITICAL_NEED_THRESHOLD:
        need_scores.append((hunger, FOOD_ITEM_TYPES))
    if thirst >= _CRITICAL_NEED_THRESHOLD:
        need_scores.append((thirst, DRINK_ITEM_TYPES))
    if injury >= _CRITICAL_NEED_THRESHOLD:
        need_scores.append((injury, HEAL_ITEM_TYPES))

    if not need_scores:
        return MIN_LOAN_AMOUNT

    # Sort by severity desc so we prioritise the most critical need first.
    need_scores.sort(key=lambda x: -x[0])

    best_price: Optional[float] = None
    for _, candidate_types in need_scores:
        prices = [
            int(ITEM_TYPES[t].get("value", 0)) * 1.5
            for t in candidate_types
            if t in ITEM_TYPES
        ]
        if prices:
            item_cost = min(prices)
            if best_price is None or item_cost < best_price:
                best_price = item_cost
        if best_price is not None:
            break

    if best_price is None:
        return MIN_LOAN_AMOUNT

    loan_amount = best_price * 1.2
    return float(max(MIN_LOAN_AMOUNT, min(MAX_LOAN_AMOUNT, loan_amount)))


# ---------------------------------------------------------------------------
# Plan step builder
# ---------------------------------------------------------------------------

def plan_take_survival_loan(
    npc: dict[str, Any],
    state: dict[str, Any],
    trader_npc: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Return a plan step dict if the NPC is eligible for a survival loan.

    Returns ``None`` if not eligible.
    """
    if not can_take_survival_loan(npc, state, trader_npc):
        return None

    amount = calculate_loan_amount(npc, state)
    trader_id = str(trader_npc.get("id") or trader_npc.get("agent_id") or "")
    return {
        "action": "take_survival_loan",
        "trader_id": trader_id,
        "amount": amount,
        "purpose": "survival_loan",
    }
