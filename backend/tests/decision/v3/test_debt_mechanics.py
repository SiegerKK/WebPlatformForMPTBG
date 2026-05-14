from __future__ import annotations

from app.games.zone_stalkers.decision.beliefs import build_belief_state
from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.models.objective import ObjectiveGenerationContext
from app.games.zone_stalkers.decision.needs import evaluate_need_result
from app.games.zone_stalkers.decision.objectives.generator import OBJECTIVE_LEAVE_ZONE, generate_objectives
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
from app.games.zone_stalkers.rules.tick_rules import tick_zone_map


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


def test_rollover_updates_agent_economic_state_for_objective_generation() -> None:
    state = {
        "world_turn": 100,
        "world_day": 1,
        "world_hour": 12,
        "world_minute": 0,
        "emission_active": False,
        "emission_scheduled_turn": None,
        "emission_ends_turn": None,
        "agents": {
            "bot1": {
                "id": "bot1",
                "name": "bot1",
                "controller": {"kind": "bot"},
                "is_alive": True,
                "has_left_zone": False,
                "location_id": "loc_a",
                "hp": 100,
                "max_hp": 100,
                "radiation": 0,
                "hunger": 10,
                "thirst": 10,
                "sleepiness": 10,
                "money": 250,
                "equipment": {"weapon": {"type": "pistol", "value": 300}, "armor": {"type": "leather_jacket", "value": 200}},
                "inventory": [{"id": "food", "type": "bread", "value": 0}, {"id": "water", "type": "water", "value": 0}],
                "action_queue": [],
                "scheduled_action": None,
                "action_used": False,
                "global_goal": "get_rich",
                "material_threshold": 1000,
                "risk_tolerance": 0.5,
            }
        },
        "traders": {
            "trader_1": {
                "id": "trader_1",
                "name": "Trader",
                "location_id": "loc_a",
                "is_alive": True,
                "money": 5000,
                "accounts_receivable": 0,
                "inventory": [],
            }
        },
        "locations": {
            "loc_a": {
                "id": "loc_a",
                "name": "A",
                "terrain_type": "buildings",
                "anomaly_activity": 0,
                "connections": [{"to": "loc_exit", "travel_time": 2}],
                "items": [],
                "agents": ["bot1", "trader_1"],
            },
            "loc_exit": {
                "id": "loc_exit",
                "name": "Exit",
                "terrain_type": "buildings",
                "anomaly_activity": 0,
                "exit_zone": True,
                "connections": [{"to": "loc_a", "travel_time": 2}],
                "items": [],
                "agents": [],
            },
        },
        "combat_interactions": {},
        "relations": {},
        "groups": {},
    }

    account = advance_survival_credit(
        state=state,
        debtor_id="bot1",
        creditor_id="trader_1",
        creditor_type="trader",
        amount=4900,
        purpose="survival_food",
        location_id="loc_a",
        world_turn=100,
    )
    account["next_due_turn"] = 101

    state, _events = tick_zone_map(state)

    econ = state["agents"]["bot1"].get("economic_state") or {}
    assert int(econ.get("debt_total") or 0) >= DEBT_ESCAPE_THRESHOLD
    assert bool(econ.get("should_escape_zone_due_to_debt")) is True

    agent = state["agents"]["bot1"]
    ctx = build_agent_context("bot1", agent, state)
    belief = build_belief_state(ctx, agent, state["world_turn"])
    need_result = evaluate_need_result(ctx, state)
    objectives = generate_objectives(ObjectiveGenerationContext(
        agent_id="bot1",
        world_turn=state["world_turn"],
        belief_state=belief,
        need_result=need_result,
        active_plan_summary=None,
        personality=agent,
    ))
    assert any(obj.key == OBJECTIVE_LEAVE_ZONE and obj.source == "debt_escape" for obj in objectives)
