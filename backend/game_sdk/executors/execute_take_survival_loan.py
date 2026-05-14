"""execute_take_survival_loan — executor for the take_survival_loan plan step.

Transfers loan money from a trader to a needy NPC and records the debt in the
global debt ledger.

Public API
----------
execute_take_survival_loan(npc, state, step, current_tick) -> dict
"""
from __future__ import annotations

from typing import Any

from game_sdk.debt_ledger import create_debt
from game_sdk.plan_steps.take_survival_loan import (
    can_take_survival_loan,
    calculate_loan_amount,
)


def execute_take_survival_loan(
    npc: dict[str, Any],
    state: dict[str, Any],
    step: dict[str, Any],
    current_tick: int,
) -> dict[str, Any]:
    """Execute the take_survival_loan step.

    Parameters
    ----------
    npc
        Mutable NPC agent dict (will have money added).
    state
        Mutable world state (debt_ledger is updated here).
    step
        Plan step payload dict with keys: ``trader_id``, ``amount``,
        ``purpose``.
    current_tick
        Current world turn / tick number.

    Returns
    -------
    dict
        Result dict with ``status`` key:
        - ``{"status": "success", "debt_id": str, "amount": float}``
        - ``{"status": "skipped", "reason": "not_eligible"}``
        - ``{"status": "failed", "reason": "trader_not_found"}``
    """
    trader_id = str(step.get("trader_id") or "")

    # 1. Locate trader
    traders = state.get("traders") or {}
    agents = state.get("agents") or {}
    trader_npc: dict[str, Any] | None = traders.get(trader_id) or agents.get(trader_id)
    if trader_npc is None:
        return {"status": "failed", "reason": "trader_not_found"}

    # 2. Re-check eligibility
    if not can_take_survival_loan(npc, state, trader_npc):
        return {"status": "skipped", "reason": "not_eligible"}

    # 3. Calculate amount
    amount = calculate_loan_amount(npc, state)
    # Respect trader's available money — never lend more than the trader has.
    trader_money = float(trader_npc.get("money") or 0)
    amount = min(amount, trader_money)
    if amount <= 0:
        return {"status": "skipped", "reason": "not_eligible"}

    # 4. Transfer money
    trader_npc["money"] = trader_money - amount
    npc["money"] = float(npc.get("money") or 0) + amount

    # 5. Record debt
    npc_id = str(npc.get("id") or npc.get("agent_id") or "")
    creditor_id = str(trader_npc.get("id") or trader_npc.get("agent_id") or trader_id)
    purpose = str(step.get("purpose") or "survival_loan")
    debt = create_debt(
        state,
        debtor_id=npc_id,
        creditor_id=creditor_id,
        amount=amount,
        purpose=purpose,
        created_tick=current_tick,
    )

    return {"status": "success", "debt_id": debt["id"], "amount": amount}
