"""debt_ledger — generic debt ledger for NPC-to-NPC lending.

The ledger lives at ``state["debt_ledger"]`` and is game-agnostic: any
agent can lend to any other agent.  In the first implementation only
trader→NPC survival loans are created at runtime; the data model is ready
for arbitrary lending.

Public API
----------
ensure_debt_ledger(state)
create_debt(state, debtor_id, creditor_id, amount, purpose, created_tick, *, due_tick=None, metadata=None) -> DebtContract
get_debts_for_debtor(state, debtor_id) -> list[DebtContract]
get_debts_for_creditor(state, creditor_id) -> list[DebtContract]
get_active_debts_for_debtor(state, debtor_id) -> list[DebtContract]
total_debt_balance(state, debtor_id) -> float
apply_repayment(state, debt_id, amount, tick) -> float
default_overdue_debts(state, current_tick) -> None  (stub)
"""
from __future__ import annotations

import uuid
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def _new_debt_id() -> str:
    return "debt_" + uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def ensure_debt_ledger(state: dict[str, Any]) -> None:
    """Initialise ``state["debt_ledger"]`` if absent."""
    if "debt_ledger" not in state:
        state["debt_ledger"] = {
            "version": 1,
            "debts": {},
            "by_debtor": {},
            "by_creditor": {},
        }


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

def create_debt(
    state: dict[str, Any],
    debtor_id: str,
    creditor_id: str,
    amount: float,
    purpose: str,
    created_tick: int,
    *,
    due_tick: Optional[int] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Allocate a new DebtContract and insert it into the ledger.

    Returns the created DebtContract dict.
    """
    ensure_debt_ledger(state)
    ledger = state["debt_ledger"]

    debt_id = _new_debt_id()
    contract: dict[str, Any] = {
        "id": debt_id,
        "debtor_id": debtor_id,
        "creditor_id": creditor_id,
        "principal": float(amount),
        "balance": float(amount),
        "interest_rate": 0.0,
        "status": "active",
        "created_tick": created_tick,
        "due_tick": due_tick,
        "repayment_history": [],
        "purpose": purpose,
        "metadata": dict(metadata) if metadata else {},
    }

    ledger["debts"][debt_id] = contract

    # Update debtor index
    if debtor_id not in ledger["by_debtor"]:
        ledger["by_debtor"][debtor_id] = []
    ledger["by_debtor"][debtor_id].append(debt_id)

    # Update creditor index
    if creditor_id not in ledger["by_creditor"]:
        ledger["by_creditor"][creditor_id] = []
    ledger["by_creditor"][creditor_id].append(debt_id)

    return contract


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def get_debts_for_debtor(
    state: dict[str, Any],
    debtor_id: str,
) -> list[dict[str, Any]]:
    """Return all DebtContracts (any status) where the debtor is *debtor_id*."""
    ensure_debt_ledger(state)
    ledger = state["debt_ledger"]
    debt_ids = ledger["by_debtor"].get(debtor_id, [])
    return [ledger["debts"][d] for d in debt_ids if d in ledger["debts"]]


def get_debts_for_creditor(
    state: dict[str, Any],
    creditor_id: str,
) -> list[dict[str, Any]]:
    """Return all DebtContracts (any status) where the creditor is *creditor_id*."""
    ensure_debt_ledger(state)
    ledger = state["debt_ledger"]
    debt_ids = ledger["by_creditor"].get(creditor_id, [])
    return [ledger["debts"][d] for d in debt_ids if d in ledger["debts"]]


def get_active_debts_for_debtor(
    state: dict[str, Any],
    debtor_id: str,
) -> list[dict[str, Any]]:
    """Return active DebtContracts for *debtor_id*, oldest first."""
    debts = get_debts_for_debtor(state, debtor_id)
    active = [d for d in debts if d.get("status") == "active"]
    return sorted(active, key=lambda d: int(d.get("created_tick") or 0))


def total_debt_balance(state: dict[str, Any], debtor_id: str) -> float:
    """Sum of ``balance`` over all active debts for *debtor_id*."""
    return sum(d["balance"] for d in get_active_debts_for_debtor(state, debtor_id))


# ---------------------------------------------------------------------------
# Repayment
# ---------------------------------------------------------------------------

def apply_repayment(
    state: dict[str, Any],
    debt_id: str,
    amount: float,
    tick: int,
) -> float:
    """Reduce the balance of *debt_id* by *amount*.

    Appends a repayment history entry.  Marks the contract as ``"repaid"``
    if the resulting balance is ≤ 0.

    Returns the new balance.
    """
    ensure_debt_ledger(state)
    ledger = state["debt_ledger"]
    contract = ledger["debts"].get(debt_id)
    if contract is None:
        return 0.0

    contract["balance"] = max(0.0, float(contract["balance"]) - float(amount))
    contract["repayment_history"].append({"tick": tick, "amount": float(amount)})

    if contract["balance"] <= 0.0:
        contract["status"] = "repaid"

    return contract["balance"]


# ---------------------------------------------------------------------------
# Maintenance (stub)
# ---------------------------------------------------------------------------

def default_overdue_debts(state: dict[str, Any], current_tick: int) -> None:
    """Mark overdue debts as ``"defaulted"``.

    TODO: implement collection / notification logic when needed.
    """
    pass
