from __future__ import annotations

from app.games.zone_stalkers.economy.debts import (
    DEBT_STATUS_DEBTOR_DEAD,
    DEBT_STATUS_DEBTOR_LEFT_ZONE,
    DEBT_STATUS_ESCAPED,
    SURVIVAL_CREDIT_ROLLOVER_TURNS,
    advance_survival_credit,
    apply_due_rollovers_with_affected_debtors,
    can_request_survival_credit,
    get_debtor_debt_total,
)


def _state() -> dict:
    return {
        "world_turn": 100,
        "agents": {
            "bot1": {"id": "bot1", "is_alive": True, "has_left_zone": False, "money": 0},
        },
        "traders": {
            "trader_1": {"id": "trader_1", "is_alive": True, "money": 1000, "accounts_receivable": 0},
        },
    }


def _account(state: dict) -> dict:
    return advance_survival_credit(
        state=state,
        debtor_id="bot1",
        creditor_id="trader_1",
        creditor_type="trader",
        amount=100,
        purpose="survival_food",
        location_id="loc_a",
        world_turn=100,
    )


def test_dead_debtor_account_does_not_rollover() -> None:
    state = _state()
    account = _account(state)
    state["agents"]["bot1"]["is_alive"] = False

    events, _ = apply_due_rollovers_with_affected_debtors(
        state=state,
        world_turn=100 + SURVIVAL_CREDIT_ROLLOVER_TURNS,
    )

    assert account["status"] == DEBT_STATUS_DEBTOR_DEAD
    assert account["next_due_turn"] is None
    assert not any(ev.get("event_type") == "debt_rolled_over" for ev in events)


def test_left_zone_debtor_account_does_not_rollover() -> None:
    state = _state()
    account = _account(state)
    state["agents"]["bot1"]["has_left_zone"] = True

    events, _ = apply_due_rollovers_with_affected_debtors(
        state=state,
        world_turn=100 + SURVIVAL_CREDIT_ROLLOVER_TURNS,
    )

    assert account["status"] == DEBT_STATUS_DEBTOR_LEFT_ZONE
    assert account["next_due_turn"] is None
    assert any(ev.get("event_type") == "debt_account_frozen" for ev in events)


def test_rollover_guard_freezes_escaped_debtor() -> None:
    state = _state()
    account = _account(state)
    state["agents"]["bot1"]["escaped_due_to_debt"] = True

    apply_due_rollovers_with_affected_debtors(
        state=state,
        world_turn=100 + SURVIVAL_CREDIT_ROLLOVER_TURNS,
    )

    assert account["status"] == DEBT_STATUS_ESCAPED
    assert account["next_due_turn"] is None


def test_no_rollover_growth_when_all_agents_dead() -> None:
    state = _state()
    _account(state)
    state["agents"]["bot1"]["is_alive"] = False

    total_before = get_debtor_debt_total(state, "bot1", world_turn=100)
    total_after = get_debtor_debt_total(state, "bot1", world_turn=100 + SURVIVAL_CREDIT_ROLLOVER_TURNS * 100)

    assert total_after == total_before


def test_can_request_survival_credit_rejects_terminal_debtor_states() -> None:
    state = _state()
    debtor = state["agents"]["bot1"]
    creditor = state["traders"]["trader_1"]

    debtor["has_left_zone"] = True
    ok, reason = can_request_survival_credit(
        state=state,
        debtor=debtor,
        creditor=creditor,
        creditor_type="trader",
        item_category="food",
        required_price=60,
        world_turn=100,
    )
    assert ok is False
    assert reason == "debtor_left_zone"

    debtor["has_left_zone"] = False
    debtor["debt_escape_completed"] = True
    ok, reason = can_request_survival_credit(
        state=state,
        debtor=debtor,
        creditor=creditor,
        creditor_type="trader",
        item_category="food",
        required_price=60,
        world_turn=100,
    )
    assert ok is False
    assert reason == "debtor_escaped"


def test_can_request_survival_credit_rejects_debt_escape_pending() -> None:
    state = _state()
    debtor = state["agents"]["bot1"]
    creditor = state["traders"]["trader_1"]
    debtor["debt_escape_pending"] = True

    ok, reason = can_request_survival_credit(
        state=state,
        debtor=debtor,
        creditor=creditor,
        creditor_type="trader",
        item_category="food",
        required_price=60,
        world_turn=100,
    )

    assert ok is False
    assert reason == "debtor_escape_pending"
