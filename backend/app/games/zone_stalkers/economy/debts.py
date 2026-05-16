from __future__ import annotations

import math
import uuid
from typing import Any

SURVIVAL_CREDIT_ROLLOVER_RATE = 0.20
SURVIVAL_CREDIT_ROLLOVER_TURNS = 1440

DEBT_ESCAPE_THRESHOLD = 5000

DEBT_REPAYMENT_MIN_PAYMENT = 10
DEBT_REPAYMENT_KEEP_SURVIVAL_RESERVE = 120
DEBT_REPAYMENT_KEEP_TRAVEL_RESERVE = 60
DEBT_REPAYMENT_URGENT_DUE_WITHIN_TURNS = 180

SURVIVAL_CREDIT_ALLOWED_CATEGORIES = frozenset({
    "drink",
    "food",
    "medical",
})

SURVIVAL_CREDIT_PURPOSE_BY_CATEGORY = {
    "drink": "survival_drink",
    "food": "survival_food",
    "medical": "survival_medical",
}

# ── Backward-compatible aliases (legacy tests/callers) ───────────────────────
SURVIVAL_LOAN_DAILY_INTEREST_RATE = SURVIVAL_CREDIT_ROLLOVER_RATE
SURVIVAL_LOAN_DUE_TURNS = SURVIVAL_CREDIT_ROLLOVER_TURNS
SURVIVAL_LOAN_REPAYMENT_RESERVE_MONEY = DEBT_REPAYMENT_KEEP_SURVIVAL_RESERVE
SURVIVAL_LOAN_MAX_PRINCIPAL = 10_000_000
SURVIVAL_LOAN_MIN_PRINCIPAL = 1
SURVIVAL_LOAN_MAX_ACTIVE_TOTAL = 10_000_000
SURVIVAL_LOAN_ALLOWED_CATEGORIES = SURVIVAL_CREDIT_ALLOWED_CATEGORIES
SURVIVAL_LOAN_PURPOSE_BY_CATEGORY = SURVIVAL_CREDIT_PURPOSE_BY_CATEGORY

_ACTIVE_ACCOUNT_STATUSES = frozenset({"active"})
DEBT_STATUS_ACTIVE = "active"
DEBT_STATUS_REPAID = "repaid"
DEBT_STATUS_ESCAPED = "escaped"
DEBT_STATUS_DEBTOR_DEAD = "debtor_dead"
DEBT_STATUS_DEBTOR_LEFT_ZONE = "debtor_left_zone"
DEBT_STATUS_UNCOLLECTABLE = "uncollectable"

_TERMINAL_ACCOUNT_STATUSES = frozenset({
    DEBT_STATUS_REPAID,
    DEBT_STATUS_ESCAPED,
    DEBT_STATUS_DEBTOR_DEAD,
    DEBT_STATUS_DEBTOR_LEFT_ZONE,
    DEBT_STATUS_UNCOLLECTABLE,
})
_NON_REOPENABLE_ACCOUNT_STATUSES = frozenset({
    DEBT_STATUS_ESCAPED,
    DEBT_STATUS_DEBTOR_DEAD,
    DEBT_STATUS_DEBTOR_LEFT_ZONE,
    DEBT_STATUS_UNCOLLECTABLE,
})


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _normalize_account(account: dict[str, Any], *, world_turn: int) -> dict[str, Any]:
    account["id"] = str(account.get("id") or f"debt_account_{uuid.uuid4().hex[:12]}")
    account["debtor_id"] = str(account.get("debtor_id") or "")
    account["creditor_id"] = str(account.get("creditor_id") or "")
    account["debtor_type"] = str(account.get("debtor_type") or "agent")
    account["creditor_type"] = str(account.get("creditor_type") or "trader")
    account["outstanding_total"] = max(0, _safe_int(account.get("outstanding_total"), 0))
    account["principal_advanced_total"] = max(0, _safe_int(account.get("principal_advanced_total"), account["outstanding_total"]))
    account["repaid_total"] = max(0, _safe_int(account.get("repaid_total"), 0))
    account["credit_advance_count"] = max(0, _safe_int(account.get("credit_advance_count"), 0))
    account["rollover_added_total"] = max(0, _safe_int(account.get("rollover_added_total"), 0))
    account["daily_rollover_rate"] = float(account.get("daily_rollover_rate") or SURVIVAL_CREDIT_ROLLOVER_RATE)
    account["created_turn"] = _safe_int(account.get("created_turn"), world_turn)
    account["last_advanced_turn"] = _safe_int(account.get("last_advanced_turn"), world_turn)
    account["last_payment_turn"] = account.get("last_payment_turn")
    account["last_rollover_turn"] = account.get("last_rollover_turn")
    account["closed_turn"] = account.get("closed_turn")
    account["closure_reason"] = account.get("closure_reason")
    next_due_turn = account.get("next_due_turn")
    account["next_due_turn"] = _safe_int(next_due_turn, world_turn + SURVIVAL_CREDIT_ROLLOVER_TURNS) if next_due_turn is not None else world_turn + SURVIVAL_CREDIT_ROLLOVER_TURNS
    account["rollover_count"] = max(0, _safe_int(account.get("rollover_count"), 0))
    if account["outstanding_total"] <= 0:
        account["status"] = DEBT_STATUS_REPAID
    else:
        status = str(account.get("status") or DEBT_STATUS_ACTIVE)
        account["status"] = status if status in (_ACTIVE_ACCOUNT_STATUSES | _TERMINAL_ACCOUNT_STATUSES) else DEBT_STATUS_ACTIVE
    purposes = account.get("purposes")
    account["purposes"] = dict(purposes) if isinstance(purposes, dict) else {}
    account["created_location_id"] = str(account.get("created_location_id") or "")
    account["source"] = str(account.get("source") or "trader_survival_credit")
    notes = account.get("notes")
    account["notes"] = dict(notes) if isinstance(notes, dict) else {}
    return account


def _freeze_account(
    *,
    account: dict[str, Any],
    world_turn: int,
    status: str,
    reason: str,
) -> dict[str, Any] | None:
    if str(account.get("status") or "") not in _ACTIVE_ACCOUNT_STATUSES:
        return None
    account["status"] = str(status)
    account["closed_turn"] = int(world_turn)
    account["closure_reason"] = str(reason)
    account["next_due_turn"] = None
    return {
        "event_type": "debt_account_frozen",
        "payload": {
            "account_id": str(account.get("id") or ""),
            "debtor_id": str(account.get("debtor_id") or ""),
            "creditor_id": str(account.get("creditor_id") or ""),
            "status": str(status),
            "reason": str(reason),
            "outstanding_total": _safe_int(account.get("outstanding_total"), 0),
            "world_turn": int(world_turn),
        },
    }


def freeze_debtor_accounts(
    *,
    state: dict[str, Any],
    debtor_id: str,
    world_turn: int,
    status: str,
    reason: str,
) -> list[dict[str, Any]]:
    ledger = ensure_debt_ledger(state, world_turn=world_turn)
    account_ids = (ledger.get("by_debtor") or {}).get(str(debtor_id), []) or []
    events: list[dict[str, Any]] = []
    for account_id in account_ids:
        account = (ledger.get("accounts") or {}).get(str(account_id))
        if not isinstance(account, dict):
            continue
        event = _freeze_account(
            account=account,
            world_turn=int(world_turn),
            status=str(status),
            reason=str(reason),
        )
        if event is not None:
            events.append(event)
    if debtor_id:
        _update_debtor_summary(state, str(debtor_id), world_turn=int(world_turn))
    return events


def _rebuild_indexes(ledger: dict[str, Any]) -> None:
    by_debtor: dict[str, list[str]] = {}
    by_creditor: dict[str, list[str]] = {}
    by_pair: dict[str, str] = {}
    accounts = ledger.get("accounts")
    if not isinstance(accounts, dict):
        accounts = {}
        ledger["accounts"] = accounts
    for account_id, account in accounts.items():
        if not isinstance(account, dict):
            continue
        debtor_id = str(account.get("debtor_id") or "")
        creditor_id = str(account.get("creditor_id") or "")
        if not debtor_id or not creditor_id:
            continue
        by_debtor.setdefault(debtor_id, []).append(str(account_id))
        by_creditor.setdefault(creditor_id, []).append(str(account_id))
        by_pair[f"{debtor_id}:{creditor_id}"] = str(account_id)
    ledger["by_debtor"] = by_debtor
    ledger["by_creditor"] = by_creditor
    ledger["by_pair"] = by_pair


def _migrate_v1_to_v2(ledger: dict[str, Any], *, world_turn: int) -> None:
    debts = ledger.get("debts")
    if not isinstance(debts, dict) or not debts:
        ledger["version"] = 2
        ledger.setdefault("accounts", {})
        _rebuild_indexes(ledger)
        return

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for debt in debts.values():
        if not isinstance(debt, dict):
            continue
        debtor_id = str(debt.get("debtor_id") or "")
        creditor_id = str(debt.get("creditor_id") or "")
        if not debtor_id or not creditor_id:
            continue
        grouped.setdefault((debtor_id, creditor_id), []).append(debt)

    accounts: dict[str, dict[str, Any]] = {}
    for (debtor_id, creditor_id), rows in grouped.items():
        outstanding_total = 0
        principal_total = 0
        repaid_total = 0
        rollover_total = 0
        due_candidates: list[int] = []
        status = "repaid"
        created_turn = world_turn
        last_advanced_turn = world_turn
        creditor_type = "trader"
        debtor_type = "agent"
        created_location_id = ""
        purposes: dict[str, int] = {}
        for row in rows:
            outstanding_principal = float(row.get("outstanding_principal") or row.get("principal") or 0.0)
            accrued_interest = float(row.get("accrued_interest") or 0.0)
            total_due = int(math.ceil(max(0.0, outstanding_principal + accrued_interest)))
            outstanding_total += total_due
            principal_total += max(0, _safe_int(row.get("principal"), 0))
            repaid_total += max(0, _safe_int(row.get("total_repaid"), 0))
            rollover_total += max(0, _safe_int(row.get("rollover_added_total"), 0))
            row_status = str(row.get("status") or "")
            if row_status in {"active", "overdue"} and total_due > 0:
                status = "active"
            due_turn = row.get("due_turn")
            if row_status in {"active", "overdue"} and due_turn is not None:
                due_candidates.append(_safe_int(due_turn, world_turn + SURVIVAL_CREDIT_ROLLOVER_TURNS))
            created_turn = min(created_turn, _safe_int(row.get("created_turn"), world_turn))
            last_advanced_turn = max(last_advanced_turn, _safe_int(row.get("created_turn"), world_turn))
            creditor_type = str(row.get("creditor_type") or creditor_type)
            debtor_type = str(row.get("debtor_type") or debtor_type)
            if not created_location_id:
                created_location_id = str(row.get("created_location_id") or "")
            purpose = str(row.get("purpose") or "")
            if purpose:
                purposes[purpose] = int(purposes.get(purpose, 0)) + 1

        account_id = f"debt_account_{uuid.uuid4().hex[:12]}"
        next_due_turn = min(due_candidates) if due_candidates else world_turn + SURVIVAL_CREDIT_ROLLOVER_TURNS
        account = {
            "id": account_id,
            "debtor_id": debtor_id,
            "debtor_type": debtor_type,
            "creditor_id": creditor_id,
            "creditor_type": creditor_type,
            "outstanding_total": max(0, int(outstanding_total)),
            "principal_advanced_total": max(0, int(principal_total)),
            "repaid_total": max(0, int(repaid_total)),
            "credit_advance_count": len(rows) if principal_total > 0 else 0,
            "rollover_added_total": max(0, int(rollover_total)),
            "daily_rollover_rate": SURVIVAL_CREDIT_ROLLOVER_RATE,
            "created_turn": created_turn,
            "last_advanced_turn": last_advanced_turn,
            "last_payment_turn": None,
            "last_rollover_turn": None,
            "next_due_turn": int(next_due_turn),
            "rollover_count": 0,
            "status": status if outstanding_total > 0 else "repaid",
            "purposes": purposes,
            "created_location_id": created_location_id,
            "source": "trader_survival_credit" if creditor_type == "trader" else "generic_credit",
            "notes": {},
        }
        accounts[account_id] = _normalize_account(account, world_turn=world_turn)

    legacy_copy = {key: dict(value) for key, value in debts.items() if isinstance(value, dict)}
    ledger["legacy_debts"] = legacy_copy
    ledger.pop("debts", None)
    ledger["accounts"] = accounts
    ledger["version"] = 2
    _rebuild_indexes(ledger)


def ensure_debt_ledger(state: dict[str, Any], *, world_turn: int | None = None) -> dict[str, Any]:
    wt = _safe_int(world_turn, _safe_int(state.get("world_turn"), 0))
    ledger = state.get("debt_ledger")
    if not isinstance(ledger, dict):
        ledger = {}
        state["debt_ledger"] = ledger

    version = _safe_int(ledger.get("version"), 1)
    if version < 2 or isinstance(ledger.get("debts"), dict):
        _migrate_v1_to_v2(ledger, world_turn=wt)
    else:
        ledger.setdefault("version", 2)
        if not isinstance(ledger.get("accounts"), dict):
            ledger["accounts"] = {}
        for key, raw in list((ledger.get("accounts") or {}).items()):
            if isinstance(raw, dict):
                ledger["accounts"][key] = _normalize_account(raw, world_turn=wt)
            else:
                ledger["accounts"].pop(key, None)
        _rebuild_indexes(ledger)

    return ledger


def get_or_create_debt_account(
    *,
    state: dict[str, Any],
    debtor_id: str,
    creditor_id: str,
    creditor_type: str,
    debtor_type: str = "agent",
    location_id: str,
    world_turn: int,
) -> dict[str, Any]:
    ledger = ensure_debt_ledger(state, world_turn=world_turn)
    pair_key = f"{debtor_id}:{creditor_id}"
    account_id = str((ledger.get("by_pair") or {}).get(pair_key) or "")
    accounts = ledger.get("accounts") or {}
    account = accounts.get(account_id) if isinstance(accounts, dict) else None
    if isinstance(account, dict):
        account_status = str(account.get("status") or "")
        if account_status == DEBT_STATUS_REPAID:
            account["status"] = DEBT_STATUS_ACTIVE
            if account.get("next_due_turn") is None:
                account["next_due_turn"] = int(world_turn) + SURVIVAL_CREDIT_ROLLOVER_TURNS
        elif account_status in _NON_REOPENABLE_ACCOUNT_STATUSES:
            return account
        return account

    account_id = f"debt_account_{uuid.uuid4().hex[:12]}"
    account = {
        "id": account_id,
        "debtor_id": str(debtor_id),
        "debtor_type": str(debtor_type),
        "creditor_id": str(creditor_id),
        "creditor_type": str(creditor_type),
        "outstanding_total": 0,
        "principal_advanced_total": 0,
        "repaid_total": 0,
        "credit_advance_count": 0,
        "rollover_added_total": 0,
        "daily_rollover_rate": SURVIVAL_CREDIT_ROLLOVER_RATE,
        "created_turn": int(world_turn),
        "last_advanced_turn": int(world_turn),
        "last_payment_turn": None,
        "last_rollover_turn": None,
        "next_due_turn": int(world_turn) + SURVIVAL_CREDIT_ROLLOVER_TURNS,
        "rollover_count": 0,
        "status": "active",
        "purposes": {},
        "created_location_id": str(location_id),
        "source": "trader_survival_credit" if creditor_type == "trader" else "generic_credit",
        "notes": {},
    }
    account = _normalize_account(account, world_turn=world_turn)
    ledger["accounts"][account_id] = account
    _rebuild_indexes(ledger)
    return account


def apply_due_rollovers_with_affected_debtors(
    *,
    state: dict[str, Any],
    world_turn: int,
) -> tuple[list[dict[str, Any]], set[str]]:
    ledger = ensure_debt_ledger(state, world_turn=world_turn)
    events: list[dict[str, Any]] = []
    affected_debtor_ids: set[str] = set()
    for account in (ledger.get("accounts") or {}).values():
        if not isinstance(account, dict):
            continue
        if str(account.get("status") or "") not in _ACTIVE_ACCOUNT_STATUSES:
            continue
        if _safe_int(account.get("outstanding_total"), 0) <= 0:
            account["status"] = DEBT_STATUS_REPAID
            continue
        debtor_id = str(account.get("debtor_id") or "")
        debtor = (state.get("agents") or {}).get(debtor_id)
        if not isinstance(debtor, dict):
            frozen_event = _freeze_account(
                account=account,
                world_turn=world_turn,
                status=DEBT_STATUS_UNCOLLECTABLE,
                reason="debtor_missing",
            )
            if frozen_event is not None:
                events.append(frozen_event)
            continue
        if not bool(debtor.get("is_alive", True)):
            frozen_event = _freeze_account(
                account=account,
                world_turn=world_turn,
                status=DEBT_STATUS_DEBTOR_DEAD,
                reason="debtor_dead_rollover_guard",
            )
            if frozen_event is not None:
                events.append(frozen_event)
            continue
        if bool(debtor.get("has_left_zone")):
            frozen_event = _freeze_account(
                account=account,
                world_turn=world_turn,
                status=DEBT_STATUS_DEBTOR_LEFT_ZONE,
                reason="debtor_left_zone_rollover_guard",
            )
            if frozen_event is not None:
                events.append(frozen_event)
            continue
        if bool(debtor.get("debt_escape_completed")) or bool(debtor.get("escaped_due_to_debt")):
            frozen_event = _freeze_account(
                account=account,
                world_turn=world_turn,
                status=DEBT_STATUS_ESCAPED,
                reason="debtor_escaped_rollover_guard",
            )
            if frozen_event is not None:
                events.append(frozen_event)
            continue
        next_due_turn = account.get("next_due_turn")
        if next_due_turn is None:
            account["next_due_turn"] = int(world_turn) + SURVIVAL_CREDIT_ROLLOVER_TURNS
            next_due_turn = account["next_due_turn"]
        while int(world_turn) >= _safe_int(next_due_turn, int(world_turn) + SURVIVAL_CREDIT_ROLLOVER_TURNS) and _safe_int(account.get("outstanding_total"), 0) > 0:
            outstanding = _safe_int(account.get("outstanding_total"), 0)
            rate = float(account.get("daily_rollover_rate") or SURVIVAL_CREDIT_ROLLOVER_RATE)
            added = int(math.ceil(float(outstanding) * max(0.0, rate)))
            account["outstanding_total"] = outstanding + added
            account["rollover_added_total"] = _safe_int(account.get("rollover_added_total"), 0) + added
            account["rollover_count"] = _safe_int(account.get("rollover_count"), 0) + 1
            account["last_rollover_turn"] = _safe_int(account.get("next_due_turn"), int(world_turn))
            account["next_due_turn"] = _safe_int(account.get("next_due_turn"), int(world_turn)) + SURVIVAL_CREDIT_ROLLOVER_TURNS
            next_due_turn = account["next_due_turn"]
            events.append({
                "event_type": "debt_rolled_over",
                "payload": {
                    "account_id": str(account.get("id") or ""),
                    "debtor_id": str(account.get("debtor_id") or ""),
                    "creditor_id": str(account.get("creditor_id") or ""),
                    "added": added,
                    "new_total": _safe_int(account.get("outstanding_total"), 0),
                    "rollover_count": _safe_int(account.get("rollover_count"), 0),
                },
            })
            if debtor_id:
                affected_debtor_ids.add(debtor_id)
    return events, affected_debtor_ids


def apply_due_rollovers(
    *,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    events, _ = apply_due_rollovers_with_affected_debtors(state=state, world_turn=world_turn)
    return events


def advance_survival_credit(
    *,
    state: dict[str, Any],
    debtor_id: str,
    creditor_id: str,
    creditor_type: str,
    amount: int,
    purpose: str,
    location_id: str,
    world_turn: int,
) -> dict[str, Any]:
    rollover_events, affected_debtor_ids = apply_due_rollovers_with_affected_debtors(
        state=state,
        world_turn=world_turn,
    )
    del rollover_events
    refresh_debtor_economic_states(state, affected_debtor_ids, world_turn=int(world_turn))
    account = get_or_create_debt_account(
        state=state,
        debtor_id=str(debtor_id),
        creditor_id=str(creditor_id),
        creditor_type=str(creditor_type),
        debtor_type="agent",
        location_id=str(location_id),
        world_turn=int(world_turn),
    )
    amount_int = max(0, _safe_int(amount, 0))
    if str(account.get("status") or "") in _NON_REOPENABLE_ACCOUNT_STATUSES:
        return account
    account["outstanding_total"] = _safe_int(account.get("outstanding_total"), 0) + amount_int
    account["principal_advanced_total"] = _safe_int(account.get("principal_advanced_total"), 0) + amount_int
    if amount_int > 0:
        account["credit_advance_count"] = _safe_int(account.get("credit_advance_count"), 0) + 1
    account["last_advanced_turn"] = int(world_turn)
    if account.get("next_due_turn") is None:
        account["next_due_turn"] = int(world_turn) + SURVIVAL_CREDIT_ROLLOVER_TURNS
    purposes = account.get("purposes")
    if not isinstance(purposes, dict):
        purposes = {}
        account["purposes"] = purposes
    if purpose:
        purposes[str(purpose)] = _safe_int(purposes.get(str(purpose)), 0) + 1
    account["status"] = DEBT_STATUS_ACTIVE if _safe_int(account.get("outstanding_total"), 0) > 0 else DEBT_STATUS_REPAID
    _update_debtor_summary(state, str(debtor_id), world_turn=int(world_turn))
    return account


def repay_debt_account(
    *,
    state: dict[str, Any],
    debtor: dict[str, Any],
    creditor: dict[str, Any],
    account_id: str,
    amount: int,
    world_turn: int,
) -> dict[str, Any]:
    rollover_events, affected_debtor_ids = apply_due_rollovers_with_affected_debtors(
        state=state,
        world_turn=world_turn,
    )
    del rollover_events
    refresh_debtor_economic_states(state, affected_debtor_ids, world_turn=int(world_turn))
    ledger = ensure_debt_ledger(state, world_turn=world_turn)
    account = (ledger.get("accounts") or {}).get(str(account_id))
    if not isinstance(account, dict):
        return {"status": "not_found", "paid": 0, "account_id": account_id}
    if str(account.get("status") or "") not in _ACTIVE_ACCOUNT_STATUSES:
        return {"status": "inactive", "paid": 0, "account_id": account_id}

    outstanding = _safe_int(account.get("outstanding_total"), 0)
    if outstanding <= 0:
        account["status"] = DEBT_STATUS_REPAID
        return {"status": "already_repaid", "paid": 0, "account_id": account_id, "remaining_total": 0, "fully_repaid": True}

    debtor_money = _safe_int(debtor.get("money"), 0)
    payment = max(0, min(_safe_int(amount, 0), debtor_money, outstanding))
    if payment <= 0:
        return {"status": "no_funds", "paid": 0, "account_id": account_id, "remaining_total": outstanding, "fully_repaid": False}

    debtor["money"] = max(0, debtor_money - payment)
    creditor["money"] = _safe_int(creditor.get("money"), 0) + payment

    if str(account.get("creditor_type") or "") == "trader":
        creditor["accounts_receivable"] = max(0, _safe_int(creditor.get("accounts_receivable"), 0) - payment)

    account["outstanding_total"] = max(0, outstanding - payment)
    account["repaid_total"] = _safe_int(account.get("repaid_total"), 0) + payment
    account["last_payment_turn"] = int(world_turn)
    if _safe_int(account.get("outstanding_total"), 0) <= 0:
        account["outstanding_total"] = 0
        account["status"] = DEBT_STATUS_REPAID

    debtor_id = str(account.get("debtor_id") or debtor.get("id") or debtor.get("agent_id") or "")
    if debtor_id:
        _update_debtor_summary(state, debtor_id, world_turn=int(world_turn))

    return {
        "status": "ok",
        "paid": payment,
        "remaining_total": _safe_int(account.get("outstanding_total"), 0),
        "fully_repaid": bool(str(account.get("status") or "") == "repaid"),
    }


def choose_debt_repayment_amount(
    *,
    debtor: dict[str, Any],
    account: dict[str, Any],
    world_turn: int,
    critical_needs: bool,
) -> int:
    remaining = _safe_int(account.get("outstanding_total"), 0)
    if remaining <= 0:
        return 0
    if remaining >= DEBT_ESCAPE_THRESHOLD:
        return 0

    money = _safe_int(debtor.get("money"), 0)
    reserve = DEBT_REPAYMENT_KEEP_SURVIVAL_RESERVE if critical_needs else DEBT_REPAYMENT_KEEP_TRAVEL_RESERVE
    surplus = max(0, money - reserve)
    if surplus < DEBT_REPAYMENT_MIN_PAYMENT:
        return 0
    if surplus >= remaining:
        return remaining

    turns_to_due = _safe_int(account.get("next_due_turn"), int(world_turn) + SURVIVAL_CREDIT_ROLLOVER_TURNS) - int(world_turn)
    if turns_to_due <= DEBT_REPAYMENT_URGENT_DUE_WITHIN_TURNS:
        return min(remaining, surplus)

    return min(remaining, max(DEBT_REPAYMENT_MIN_PAYMENT, surplus // 2))


def repay_debts_to_creditor_if_useful(
    *,
    state: dict[str, Any],
    debtor: dict[str, Any],
    creditor: dict[str, Any],
    world_turn: int,
    critical_needs: bool = False,
) -> list[dict[str, Any]]:
    debtor_id = str(debtor.get("id") or debtor.get("agent_id") or "")
    creditor_id = str(creditor.get("id") or creditor.get("agent_id") or "")
    if not debtor_id or not creditor_id:
        return []

    rollover_events, affected_debtor_ids = apply_due_rollovers_with_affected_debtors(
        state=state,
        world_turn=world_turn,
    )
    del rollover_events
    if affected_debtor_ids:
        refresh_debtor_economic_states(state, affected_debtor_ids, world_turn=int(world_turn))

    ledger = ensure_debt_ledger(state, world_turn=world_turn)
    pair_key = f"{debtor_id}:{creditor_id}"
    account_id = str((ledger.get("by_pair") or {}).get(pair_key) or "")
    account = (ledger.get("accounts") or {}).get(account_id)
    if not isinstance(account, dict):
        return []
    if str(account.get("status") or "") != "active":
        return []

    amount = choose_debt_repayment_amount(
        debtor=debtor,
        account=account,
        world_turn=world_turn,
        critical_needs=critical_needs,
    )
    if amount <= 0:
        return []

    result = repay_debt_account(
        state=state,
        debtor=debtor,
        creditor=creditor,
        account_id=account_id,
        amount=amount,
        world_turn=world_turn,
    )
    paid = _safe_int(result.get("paid"), 0)
    if paid <= 0:
        return []

    events: list[dict[str, Any]] = [{
        "event_type": "debt_payment",
        "payload": {
            "account_id": account_id,
            "debtor_id": debtor_id,
            "creditor_id": creditor_id,
            "amount": paid,
            "remaining_total": _safe_int(result.get("remaining_total"), _safe_int(account.get("outstanding_total"), 0)),
        },
    }]
    if bool(result.get("fully_repaid")):
        events.append({
            "event_type": "debt_repaid",
            "payload": {
                "account_id": account_id,
                "debtor_id": debtor_id,
                "creditor_id": creditor_id,
                "total_repaid": _safe_int(account.get("repaid_total"), 0),
            },
        })
    return events


def get_debtor_debt_total(
    state: dict[str, Any],
    debtor_id: str,
    *,
    world_turn: int,
) -> int:
    rollover_events, _affected_debtor_ids = apply_due_rollovers_with_affected_debtors(
        state=state,
        world_turn=world_turn,
    )
    del rollover_events, _affected_debtor_ids
    ledger = ensure_debt_ledger(state, world_turn=world_turn)
    account_ids = (ledger.get("by_debtor") or {}).get(str(debtor_id), []) or []
    total = 0
    for account_id in account_ids:
        account = (ledger.get("accounts") or {}).get(str(account_id))
        if not isinstance(account, dict):
            continue
        if str(account.get("status") or "") != "active":
            continue
        total += _safe_int(account.get("outstanding_total"), 0)
    return int(total)


def should_escape_zone_due_to_debt(
    state: dict[str, Any],
    debtor_id: str,
    *,
    world_turn: int,
) -> bool:
    return get_debtor_debt_total(state, debtor_id, world_turn=world_turn) >= DEBT_ESCAPE_THRESHOLD


def can_request_survival_credit(
    *,
    state: dict[str, Any],
    debtor: dict[str, Any],
    creditor: dict[str, Any],
    creditor_type: str,
    item_category: str,
    required_price: int,
    world_turn: int,
) -> tuple[bool, str]:
    del state, world_turn
    if item_category not in SURVIVAL_CREDIT_ALLOWED_CATEGORIES:
        return False, "unsupported_item_category"
    if not bool(debtor.get("is_alive", True)):
        return False, "debtor_not_alive"
    if bool(debtor.get("has_left_zone")):
        return False, "debtor_left_zone"
    if bool(debtor.get("debt_escape_pending")):
        return False, "debtor_escape_pending"
    if bool(debtor.get("debt_escape_completed")) or bool(debtor.get("escaped_due_to_debt")):
        return False, "debtor_escaped"
    if not isinstance(creditor, dict):
        return False, "creditor_not_found"
    if creditor_type in {"trader", "agent"} and not bool(creditor.get("is_alive", True)):
        return False, "creditor_not_alive"
    money = _safe_int(debtor.get("money"), 0)
    if _safe_int(required_price, 0) <= money:
        return False, "already_affordable"
    return True, "ok"


# ── Backward-compatible API wrappers ─────────────────────────────────────────
def can_request_survival_loan(**kwargs: Any) -> tuple[bool, str]:
    return can_request_survival_credit(**kwargs)


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
    del debtor_type, allowed_item_category, daily_interest_rate, due_turn
    account = advance_survival_credit(
        state=state,
        debtor_id=debtor_id,
        creditor_id=creditor_id,
        creditor_type=creditor_type,
        amount=principal,
        purpose=purpose,
        location_id=location_id,
        world_turn=world_turn,
    )
    return account


def repay_debt(
    *,
    state: dict[str, Any],
    debtor: dict[str, Any],
    creditor: dict[str, Any] | None,
    debt_id: str,
    amount: int,
    world_turn: int,
) -> dict[str, Any]:
    if not isinstance(creditor, dict):
        return {"status": "creditor_not_found", "paid": 0, "debt_id": debt_id}
    result = repay_debt_account(
        state=state,
        debtor=debtor,
        creditor=creditor,
        account_id=debt_id,
        amount=amount,
        world_turn=world_turn,
    )
    return {
        "status": result.get("status"),
        "debt_id": debt_id,
        "paid": _safe_int(result.get("paid"), 0),
        "remaining_total": _safe_int(result.get("remaining_total"), 0),
        "fully_repaid": bool(result.get("fully_repaid")),
    }


def auto_repay_debts(
    *,
    state: dict[str, Any],
    debtor: dict[str, Any],
    creditor: dict[str, Any] | None,
    world_turn: int,
    reserve_money: int = SURVIVAL_LOAN_REPAYMENT_RESERVE_MONEY,
) -> list[dict[str, Any]]:
    del reserve_money
    if not isinstance(creditor, dict):
        return []
    return repay_debts_to_creditor_if_useful(
        state=state,
        debtor=debtor,
        creditor=creditor,
        world_turn=world_turn,
        critical_needs=False,
    )


def get_debtor_active_debts(
    state: dict[str, Any],
    debtor_id: str,
    *,
    world_turn: int,
    creditor_id: str | None = None,
) -> list[dict[str, Any]]:
    rollover_events, _affected_debtor_ids = apply_due_rollovers_with_affected_debtors(
        state=state,
        world_turn=world_turn,
    )
    del rollover_events, _affected_debtor_ids
    ledger = ensure_debt_ledger(state, world_turn=world_turn)
    ids = (ledger.get("by_debtor") or {}).get(str(debtor_id), []) or []
    rows: list[dict[str, Any]] = []
    for account_id in ids:
        account = (ledger.get("accounts") or {}).get(str(account_id))
        if not isinstance(account, dict):
            continue
        if str(account.get("status") or "") != "active":
            continue
        if creditor_id is not None and str(account.get("creditor_id") or "") != str(creditor_id):
            continue
        rows.append(account)
    return rows


def get_debtor_outstanding_total(
    state: dict[str, Any],
    debtor_id: str,
    *,
    world_turn: int,
) -> int:
    return get_debtor_debt_total(state, debtor_id, world_turn=world_turn)


def accrue_debt_interest(debt: dict[str, Any], *, world_turn: int) -> dict[str, Any]:
    # Compatibility shim kept for callers that still import this symbol.
    del world_turn
    return debt


def mark_overdue_debts(
    state: dict[str, Any],
    *,
    world_turn: int,
) -> int:
    events = apply_due_rollovers(state=state, world_turn=world_turn)
    return len(events)


def summarize_debtor_economic_state(
    state: dict[str, Any],
    debtor_id: str,
    *,
    world_turn: int,
) -> dict[str, Any]:
    ledger = ensure_debt_ledger(state, world_turn=world_turn)
    rollover_events, _affected_debtor_ids = apply_due_rollovers_with_affected_debtors(
        state=state,
        world_turn=world_turn,
    )
    del rollover_events, _affected_debtor_ids
    account_ids = (ledger.get("by_debtor") or {}).get(str(debtor_id), []) or []
    debt_total = 0
    active_count = 0
    creditors: set[str] = set()
    max_creditor_debt = 0
    rollover_count_total = 0
    next_due_turn_min: int | None = None
    account_rows: list[dict[str, Any]] = []

    for account_id in account_ids:
        account = (ledger.get("accounts") or {}).get(str(account_id))
        if not isinstance(account, dict):
            continue
        status = str(account.get("status") or "")
        if status != "active":
            continue
        outstanding = _safe_int(account.get("outstanding_total"), 0)
        if outstanding <= 0:
            continue
        debt_total += outstanding
        active_count += 1
        creditor_id = str(account.get("creditor_id") or "")
        if creditor_id:
            creditors.add(creditor_id)
        max_creditor_debt = max(max_creditor_debt, outstanding)
        rollover_count_total += _safe_int(account.get("rollover_count"), 0)
        due_turn = account.get("next_due_turn")
        if isinstance(due_turn, (int, float)):
            due_turn_int = int(due_turn)
            next_due_turn_min = due_turn_int if next_due_turn_min is None else min(next_due_turn_min, due_turn_int)
        account_rows.append({
            "account_id": str(account.get("id") or account_id),
            "creditor_id": creditor_id,
            "outstanding_total": outstanding,
            "next_due_turn": account.get("next_due_turn"),
            "rollover_count": _safe_int(account.get("rollover_count"), 0),
            "last_payment_turn": account.get("last_payment_turn"),
        })

    return {
        "debt_total": int(debt_total),
        "active_debt_account_count": int(active_count),
        "creditors": sorted(c for c in creditors if c),
        "max_creditor_debt": int(max_creditor_debt),
        "rollover_count_total": int(rollover_count_total),
        "next_due_turn_min": next_due_turn_min,
        "debt_escape_threshold": int(DEBT_ESCAPE_THRESHOLD),
        "should_escape_zone_due_to_debt": bool(int(debt_total) >= int(DEBT_ESCAPE_THRESHOLD)),
        "debt_accounts": account_rows,
        # backward-compatible fields
        "active_debt_count": int(active_count),
        "overdue_debt_count": 0,
        "defaulted_debt_count": 0,
    }


def _update_debtor_summary(state: dict[str, Any], debtor_id: str, *, world_turn: int) -> None:
    debtor = (state.get("agents") or {}).get(str(debtor_id))
    if not isinstance(debtor, dict):
        return
    debtor["economic_state"] = summarize_debtor_economic_state(state, str(debtor_id), world_turn=world_turn)


def refresh_debtor_economic_states(
    state: dict[str, Any],
    debtor_ids: set[str] | list[str] | tuple[str, ...],
    *,
    world_turn: int,
) -> None:
    for debtor_id in debtor_ids:
        if not debtor_id:
            continue
        _update_debtor_summary(state, str(debtor_id), world_turn=world_turn)
