from __future__ import annotations

import math
import uuid
from typing import Any

SURVIVAL_LOAN_DAILY_INTEREST_RATE = 0.05
SURVIVAL_LOAN_DUE_TURNS = 1440
SURVIVAL_LOAN_MAX_PRINCIPAL = 300
SURVIVAL_LOAN_MIN_PRINCIPAL = 1
SURVIVAL_LOAN_MAX_ACTIVE_TOTAL = 500
SURVIVAL_LOAN_REPAYMENT_RESERVE_MONEY = 100

SURVIVAL_LOAN_ALLOWED_CATEGORIES = frozenset({
    "drink",
    "food",
    "medical",
})

SURVIVAL_LOAN_PURPOSE_BY_CATEGORY = {
    "drink": "survival_drink",
    "food": "survival_food",
    "medical": "survival_medical",
}

_ACTIVE_DEBT_STATUSES = frozenset({"active", "overdue"})


def ensure_debt_ledger(state: dict[str, Any]) -> dict[str, Any]:
    ledger = state.get("debt_ledger")
    if not isinstance(ledger, dict):
        ledger = {}
        state["debt_ledger"] = ledger
    ledger.setdefault("version", 1)
    if not isinstance(ledger.get("debts"), dict):
        ledger["debts"] = {}
    if not isinstance(ledger.get("by_debtor"), dict):
        ledger["by_debtor"] = {}
    if not isinstance(ledger.get("by_creditor"), dict):
        ledger["by_creditor"] = {}
    return ledger


def accrue_debt_interest(
    debt: dict[str, Any],
    *,
    world_turn: int,
) -> dict[str, Any]:
    if str(debt.get("status") or "") not in _ACTIVE_DEBT_STATUSES:
        debt["last_accrued_turn"] = int(world_turn)
        return debt
    current_turn = int(world_turn)
    last_turn = int(debt.get("last_accrued_turn", current_turn) or current_turn)
    delta_turns = max(0, current_turn - last_turn)
    if delta_turns <= 0:
        return debt
    outstanding_principal = float(debt.get("outstanding_principal") or 0.0)
    daily_interest_rate = float(debt.get("daily_interest_rate") or 0.0)
    interest_delta = outstanding_principal * daily_interest_rate * float(delta_turns) / 1440.0
    debt["accrued_interest"] = float(debt.get("accrued_interest") or 0.0) + interest_delta
    debt["last_accrued_turn"] = current_turn
    return debt


def mark_overdue_debts(
    state: dict[str, Any],
    *,
    world_turn: int,
) -> int:
    ledger = ensure_debt_ledger(state)
    changed = 0
    current_turn = int(world_turn)
    for debt in ledger["debts"].values():
        if not isinstance(debt, dict):
            continue
        due_turn = debt.get("due_turn")
        status = str(debt.get("status") or "")
        if status == "active" and due_turn is not None and current_turn > int(due_turn):
            debt["status"] = "overdue"
            changed += 1
            status = "overdue"
        if status == "overdue" and due_turn is not None and current_turn > int(due_turn) + 1440:
            debt["status"] = "defaulted"
            changed += 1
    return changed


def get_debtor_active_debts(
    state: dict[str, Any],
    debtor_id: str,
    *,
    world_turn: int,
    creditor_id: str | None = None,
) -> list[dict[str, Any]]:
    ledger = ensure_debt_ledger(state)
    mark_overdue_debts(state, world_turn=world_turn)
    ids = ledger["by_debtor"].get(str(debtor_id), []) or []
    rows: list[dict[str, Any]] = []
    for debt_id in ids:
        debt = ledger["debts"].get(debt_id)
        if not isinstance(debt, dict):
            continue
        if creditor_id is not None and str(debt.get("creditor_id") or "") != str(creditor_id):
            continue
        accrue_debt_interest(debt, world_turn=world_turn)
        if str(debt.get("status") or "") in _ACTIVE_DEBT_STATUSES:
            rows.append(debt)
    return rows


def get_debtor_outstanding_total(
    state: dict[str, Any],
    debtor_id: str,
    *,
    world_turn: int,
) -> int:
    active = get_debtor_active_debts(state, debtor_id, world_turn=world_turn)
    total = sum(float(d.get("outstanding_principal") or 0.0) + float(d.get("accrued_interest") or 0.0) for d in active)
    return int(math.ceil(total))


def can_request_survival_loan(
    *,
    state: dict[str, Any],
    debtor: dict[str, Any],
    creditor: dict[str, Any],
    creditor_type: str,
    item_category: str,
    required_price: int,
    world_turn: int,
) -> tuple[bool, str]:
    if item_category not in SURVIVAL_LOAN_ALLOWED_CATEGORIES:
        return False, "unsupported_item_category"
    if not bool(debtor.get("is_alive", True)):
        return False, "debtor_not_alive"
    if not isinstance(creditor, dict):
        return False, "creditor_not_found"
    if creditor_type == "trader" and not bool(creditor.get("is_alive", True)):
        return False, "creditor_not_alive"
    if creditor_type == "agent" and not bool(creditor.get("is_alive", True)):
        return False, "creditor_not_alive"

    debtor_id = str(debtor.get("id") or "")
    ledger = ensure_debt_ledger(state)
    ids = ledger["by_debtor"].get(debtor_id, []) or []
    has_defaulted = any(
        str((ledger["debts"].get(debt_id) or {}).get("status") or "") == "defaulted"
        for debt_id in ids
    )
    if has_defaulted:
        return False, "has_defaulted_debt"

    debt_total = get_debtor_outstanding_total(state, debtor_id, world_turn=world_turn)
    if debt_total >= SURVIVAL_LOAN_MAX_ACTIVE_TOTAL:
        return False, "debt_limit_reached"

    money = int(debtor.get("money") or 0)
    required = int(required_price or 0)
    if required <= money:
        return False, "already_affordable"
    principal = required - money
    if principal < SURVIVAL_LOAN_MIN_PRINCIPAL:
        return False, "principal_below_min"
    if principal > SURVIVAL_LOAN_MAX_PRINCIPAL:
        return False, "principal_too_high"

    return True, "ok"


def create_debt(
    *,
    state: dict[str, Any],
    debtor_id: str,
    creditor_id: str,
    creditor_type: str,
    debtor_type: str = "agent",
    principal: int,
    purpose: str,
    allowed_item_category: str,
    location_id: str,
    daily_interest_rate: float,
    due_turn: int,
    world_turn: int,
) -> dict[str, Any]:
    ledger = ensure_debt_ledger(state)
    debt_id = f"debt_{uuid.uuid4().hex[:12]}"
    principal_int = int(max(0, principal))
    debt = {
        "id": debt_id,
        "debtor_id": str(debtor_id),
        "debtor_type": str(debtor_type),
        "creditor_id": str(creditor_id),
        "creditor_type": str(creditor_type),
        "principal": principal_int,
        "outstanding_principal": principal_int,
        "accrued_interest": 0.0,
        "total_repaid": 0,
        "daily_interest_rate": float(daily_interest_rate),
        "created_turn": int(world_turn),
        "last_accrued_turn": int(world_turn),
        "due_turn": int(due_turn),
        "purpose": str(purpose),
        "allowed_item_category": str(allowed_item_category),
        "created_location_id": str(location_id),
        "status": "active",
        "collateral_item_ids": [],
        "source": "trader_survival_credit" if creditor_type == "trader" else "generic_credit",
        "notes": {},
    }
    ledger["debts"][debt_id] = debt
    ledger["by_debtor"].setdefault(str(debtor_id), []).append(debt_id)
    ledger["by_creditor"].setdefault(str(creditor_id), []).append(debt_id)

    _update_debtor_summary(state, str(debtor_id), world_turn=int(world_turn))
    return debt


def repay_debt(
    *,
    state: dict[str, Any],
    debtor: dict[str, Any],
    creditor: dict[str, Any] | None,
    debt_id: str,
    amount: int,
    world_turn: int,
) -> dict[str, Any]:
    ledger = ensure_debt_ledger(state)
    debt = ledger["debts"].get(str(debt_id))
    if not isinstance(debt, dict):
        return {"status": "not_found", "paid": 0, "debt_id": debt_id}
    if str(debt.get("status") or "") not in _ACTIVE_DEBT_STATUSES:
        return {"status": "inactive", "paid": 0, "debt_id": debt_id}
    accrue_debt_interest(debt, world_turn=world_turn)

    debtor_money = int(debtor.get("money") or 0)
    budget = min(int(amount), debtor_money)
    if budget <= 0:
        return {"status": "no_funds", "paid": 0, "debt_id": debt_id}

    interest_before = float(debt.get("accrued_interest") or 0.0)
    principal_before = float(debt.get("outstanding_principal") or 0.0)
    total_due = interest_before + principal_before
    if total_due <= 0:
        debt["status"] = "repaid"
        return {"status": "already_repaid", "paid": 0, "debt_id": debt_id}

    payment = min(float(budget), total_due)
    pay_interest = min(payment, interest_before)
    pay_principal = max(0.0, payment - pay_interest)

    debt["accrued_interest"] = max(0.0, interest_before - pay_interest)
    debt["outstanding_principal"] = max(0.0, principal_before - pay_principal)
    debt["total_repaid"] = int(debt.get("total_repaid") or 0) + int(math.floor(payment))
    debt["last_accrued_turn"] = int(world_turn)

    debtor["money"] = max(0, debtor_money - int(math.ceil(payment)))
    if isinstance(creditor, dict):
        creditor["money"] = int(creditor.get("money") or 0) + int(math.ceil(payment))
        if str(debt.get("creditor_type") or "") == "trader":
            creditor["accounts_receivable"] = max(
                0,
                int(creditor.get("accounts_receivable") or 0) - int(math.ceil(payment)),
            )

    if debt["accrued_interest"] <= 0.000001 and debt["outstanding_principal"] <= 0.000001:
        debt["accrued_interest"] = 0.0
        debt["outstanding_principal"] = 0.0
        debt["status"] = "repaid"

    debtor_id = str(debt.get("debtor_id") or "")
    _update_debtor_summary(state, debtor_id, world_turn=int(world_turn))
    return {
        "status": "ok",
        "debt_id": debt_id,
        "paid": int(math.ceil(payment)),
        "remaining_total": int(math.ceil(debt["accrued_interest"] + debt["outstanding_principal"])),
        "fully_repaid": bool(str(debt.get("status") or "") == "repaid"),
    }


def auto_repay_debts(
    *,
    state: dict[str, Any],
    debtor: dict[str, Any],
    creditor: dict[str, Any] | None,
    world_turn: int,
    reserve_money: int = SURVIVAL_LOAN_REPAYMENT_RESERVE_MONEY,
) -> list[dict[str, Any]]:
    debtor_money = int(debtor.get("money") or 0)
    if debtor_money <= int(reserve_money):
        return []
    debtor_id = str(debtor.get("id") or "")
    creditor_id = str((creditor or {}).get("id") or "")
    debts = get_debtor_active_debts(
        state,
        debtor_id,
        world_turn=world_turn,
        creditor_id=creditor_id or None,
    )
    if not debts:
        return []

    debts_sorted = sorted(
        debts,
        key=lambda d: (
            0 if str(d.get("status") or "") == "overdue" else 1,
            -float(d.get("daily_interest_rate") or 0.0),
            int(d.get("created_turn") or 0),
        ),
    )
    budget = debtor_money - int(reserve_money)
    if budget <= 0:
        return []

    events: list[dict[str, Any]] = []
    for debt in debts_sorted:
        if budget <= 0:
            break
        debt_id = str(debt.get("id") or "")
        if not debt_id:
            continue
        result = repay_debt(
            state=state,
            debtor=debtor,
            creditor=creditor,
            debt_id=debt_id,
            amount=budget,
            world_turn=world_turn,
        )
        paid = int(result.get("paid") or 0)
        if paid <= 0:
            continue
        budget -= paid
        remaining_total = get_debtor_outstanding_total(state, debtor_id, world_turn=world_turn)
        events.append({
            "event_type": "debt_payment",
            "payload": {
                "debt_id": debt_id,
                "debtor_id": debtor_id,
                "creditor_id": str(debt.get("creditor_id") or ""),
                "amount": paid,
                "remaining_total": remaining_total,
            },
        })
        if bool(result.get("fully_repaid")):
            events.append({
                "event_type": "debt_repaid",
                "payload": {
                    "debt_id": debt_id,
                    "debtor_id": debtor_id,
                    "creditor_id": str(debt.get("creditor_id") or ""),
                    "total_repaid": int((ensure_debt_ledger(state)["debts"].get(debt_id) or {}).get("total_repaid") or 0),
                },
            })
    return events


def summarize_debtor_economic_state(
    state: dict[str, Any],
    debtor_id: str,
    *,
    world_turn: int,
) -> dict[str, Any]:
    ledger = ensure_debt_ledger(state)
    mark_overdue_debts(state, world_turn=world_turn)
    ids = ledger["by_debtor"].get(str(debtor_id), []) or []
    debt_total = 0.0
    active_count = 0
    overdue_count = 0
    defaulted_count = 0
    creditors: set[str] = set()
    for debt_id in ids:
        debt = ledger["debts"].get(debt_id)
        if not isinstance(debt, dict):
            continue
        status = str(debt.get("status") or "")
        if status in _ACTIVE_DEBT_STATUSES:
            accrue_debt_interest(debt, world_turn=world_turn)
            debt_total += float(debt.get("outstanding_principal") or 0.0) + float(debt.get("accrued_interest") or 0.0)
            active_count += 1
            creditors.add(str(debt.get("creditor_id") or ""))
            if status == "overdue":
                overdue_count += 1
        elif status == "defaulted":
            defaulted_count += 1

    return {
        "debt_total": int(math.ceil(debt_total)),
        "active_debt_count": int(active_count),
        "overdue_debt_count": int(overdue_count),
        "defaulted_debt_count": int(defaulted_count),
        "creditors": sorted(c for c in creditors if c),
    }


def _update_debtor_summary(state: dict[str, Any], debtor_id: str, *, world_turn: int) -> None:
    debtor = (state.get("agents") or {}).get(str(debtor_id))
    if not isinstance(debtor, dict):
        return
    debtor["economic_state"] = summarize_debtor_economic_state(state, str(debtor_id), world_turn=world_turn)

