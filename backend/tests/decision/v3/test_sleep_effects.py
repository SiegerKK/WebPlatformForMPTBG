"""Tests for sleep interval mechanics (PR1 finalization).

Covers:
- Sleep applies effects every 30 minutes (ticks).
- Partial recovery is kept when sleep is interrupted.
- Sleep completion does not double-apply sleepiness reset.
- Backward-compatible: old saves without interval fields work.
- Memory is only written on completion/interruption, not every interval.
- select_intent: critical hunger/thirst beats sleepiness (shared thresholds).
- _plan_rest: inserts prepare_sleep_drink/food steps when needed.
- prepare_sleep_* consume steps record correct action_kind.
"""
from __future__ import annotations

import copy
from typing import Any

import pytest

from app.games.zone_stalkers.rules.tick_rules import (
    _apply_sleep_interval_effect,
    _process_sleep_tick,
    tick_zone_map,
)
from app.games.zone_stalkers.rules.tick_constants import (
    SLEEP_EFFECT_INTERVAL_TURNS,
    SLEEPINESS_RECOVERY_PER_SLEEP_INTERVAL,
    HUNGER_INCREASE_PER_SLEEP_INTERVAL,
    THIRST_INCREASE_PER_SLEEP_INTERVAL,
    SLEEP_SAFE_HUNGER_THRESHOLD,
    SLEEP_SAFE_THIRST_THRESHOLD,
    CRITICAL_HUNGER_THRESHOLD,
    CRITICAL_THIRST_THRESHOLD,
)
from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.needs import evaluate_needs
from app.games.zone_stalkers.decision.intents import select_intent
from app.games.zone_stalkers.decision.planner import build_plan
from app.games.zone_stalkers.decision.models.intent import (
    INTENT_SEEK_FOOD,
    INTENT_SEEK_WATER,
    INTENT_REST,
)
from app.games.zone_stalkers.decision.models.plan import STEP_CONSUME_ITEM, STEP_SLEEP_FOR_HOURS
from tests.decision.conftest import make_agent, make_minimal_state
from tests.decision.v3.memory_assertions import v3_action_records, v3_records


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_base_state() -> dict:
    return {
        "seed": 1,
        "world_turn": 100,
        "world_day": 1,
        "world_hour": 12,
        "world_minute": 0,
        "emission_active": False,
        "agents": {},
        "traders": {},
        "locations": {
            "loc_a": {
                "name": "Локация А",
                "terrain_type": "buildings",
                "anomaly_activity": 0,
                "connections": [{"to": "loc_b", "travel_time": 12}],
                "items": [],
                "agents": [],
            },
            "loc_b": {
                "name": "Локация Б",
                "terrain_type": "buildings",
                "anomaly_activity": 0,
                "connections": [{"to": "loc_a", "travel_time": 12}],
                "items": [],
                "agents": [],
            },
        },
        "combat_interactions": {},
        "relations": {},
        "groups": {},
    }


def _sleeping_bot(
    sleepiness: int = 80,
    hunger: int = 20,
    thirst: int = 20,
    turns_remaining: int = 480,
    turns_total: int = 480,
    extra_inventory: list | None = None,
) -> dict:
    inv = [
        {"id": "bread1", "type": "bread", "value": 0},
        {"id": "water1", "type": "water", "value": 0},
        {"id": "ammo1", "type": "ammo_9mm", "value": 0},
        {"id": "med1", "type": "bandage", "value": 0},
    ]
    if extra_inventory:
        inv.extend(extra_inventory)
    return {
        "archetype": "stalker_agent",
        "controller": {"kind": "bot"},
        "name": "bot",
        "is_alive": True,
        "has_left_zone": False,
        "action_used": False,
        "location_id": "loc_a",
        "hp": 60,
        "max_hp": 100,
        "radiation": 10,
        "hunger": hunger,
        "thirst": thirst,
        "sleepiness": sleepiness,
        "money": 100,
        "global_goal": "get_rich",
        "material_threshold": 3000,
        "equipment": {"weapon": {"type": "pistol"}, "armor": {"type": "leather_jacket"}},
        "inventory": inv,
        "action_queue": [],
        "scheduled_action": {
            "type": "sleep",
            "hours": 8,
            "turns_remaining": turns_remaining,
            "turns_total": turns_total,
            "sleep_progress_turns": 0,
            "sleep_intervals_applied": 0,
            "sleep_turns_slept": 0,
        },
        "skill_stalker": 1,
        "risk_tolerance": 0.5,
    }


# ─── Unit: _apply_sleep_interval_effect ──────────────────────────────────────

def test_apply_sleep_interval_reduces_sleepiness():
    agent: dict[str, Any] = {"sleepiness": 80, "hunger": 20, "thirst": 30}
    sched: dict[str, Any] = {"sleep_intervals_applied": 0}
    state: dict[str, Any] = {}
    _apply_sleep_interval_effect("bot", agent, sched, state, 100)

    assert agent["sleepiness"] == 80 - SLEEPINESS_RECOVERY_PER_SLEEP_INTERVAL
    assert agent["hunger"] == 20 + HUNGER_INCREASE_PER_SLEEP_INTERVAL
    assert agent["thirst"] == 30 + THIRST_INCREASE_PER_SLEEP_INTERVAL
    assert sched["sleep_intervals_applied"] == 1


def test_apply_sleep_interval_clamps_at_zero():
    agent: dict[str, Any] = {"sleepiness": 5, "hunger": 99, "thirst": 99}
    sched: dict[str, Any] = {"sleep_intervals_applied": 0}
    _apply_sleep_interval_effect("bot", agent, sched, {}, 100)

    assert agent["sleepiness"] == 0
    assert agent["hunger"] == 100
    assert agent["thirst"] == 100


# ─── Unit: _process_sleep_tick ────────────────────────────────────────────────

def test_process_sleep_tick_accumulates_and_fires_at_interval():
    agent: dict[str, Any] = {"sleepiness": 80, "hunger": 20, "thirst": 30}
    sched: dict[str, Any] = {"sleep_progress_turns": 0, "sleep_intervals_applied": 0}

    for _ in range(SLEEP_EFFECT_INTERVAL_TURNS - 1):
        evs = _process_sleep_tick("bot", agent, sched, {}, 100)
        assert evs == []  # no interval fired yet

    # One more tick fires the interval
    evs = _process_sleep_tick("bot", agent, sched, {}, 100)
    assert len(evs) == 1
    assert evs[0]["event_type"] == "sleep_interval_applied"
    assert sched["sleep_intervals_applied"] == 1
    assert sched["sleep_progress_turns"] == 0


def test_process_sleep_tick_backward_compat_no_fields():
    """Old save without interval fields is handled via setdefault."""
    agent: dict[str, Any] = {"sleepiness": 50, "hunger": 10, "thirst": 10}
    sched: dict[str, Any] = {}  # old format — no progress/intervals fields
    # Should not raise
    for _ in range(SLEEP_EFFECT_INTERVAL_TURNS):
        _process_sleep_tick("bot", agent, sched, {}, 100)
    assert sched["sleep_intervals_applied"] == 1


def test_process_sleep_tick_sets_early_wake_when_sleepiness_zero():
    agent: dict[str, Any] = {"sleepiness": 0, "hunger": 10, "thirst": 10}
    sched: dict[str, Any] = {"sleep_progress_turns": 0, "sleep_intervals_applied": 0}
    evs = _process_sleep_tick("bot", agent, sched, {}, 100)
    assert evs == []
    assert sched.get("wake_due_to_rested") is True


# ─── Integration: full tick loop ─────────────────────────────────────────────

def test_sleep_applies_effect_every_30_ticks():
    state = _make_base_state()
    bot = _sleeping_bot(sleepiness=80)
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    # Run exactly 30 ticks
    for _ in range(SLEEP_EFFECT_INTERVAL_TURNS):
        state, _ = tick_zone_map(state)

    assert state["agents"]["bot1"]["sleepiness"] == 80 - SLEEPINESS_RECOVERY_PER_SLEEP_INTERVAL
    assert state["agents"]["bot1"]["scheduled_action"]["sleep_intervals_applied"] == 1


def test_sleep_memory_not_written_during_intervals():
    """Memory entries for sleep_completed must NOT appear mid-sleep."""
    state = _make_base_state()
    bot = _sleeping_bot(sleepiness=80)
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    for _ in range(SLEEP_EFFECT_INTERVAL_TURNS * 2):
        state, _ = tick_zone_map(state)

    sleep_completed_entries = v3_action_records(state["agents"]["bot1"], "sleep_completed")
    assert sleep_completed_entries == []  # still sleeping


def test_sleep_completion_memory_written_and_no_double_reset():
    """After sleep completes: memory entry exists, sleepiness not reset to 0 artificially."""
    turns = 3  # very short sleep for test speed
    state = _make_base_state()
    bot = _sleeping_bot(sleepiness=80, turns_remaining=turns, turns_total=turns)
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    # Run until completion
    for _ in range(turns + 1):
        state, _ = tick_zone_map(state)

    completion_entries = v3_action_records(state["agents"]["bot1"], "sleep_completed")
    assert len(completion_entries) == 1

    # Sleepiness is NOT set to 0 artificially — it's only reduced by intervals.
    # With 3 turns (< 30), no interval fired, so sleepiness should still be ~80.
    # It may have been reduced from HP/hunger ticks, so just verify it's > 0.
    assert state["agents"]["bot1"]["sleepiness"] > 0


def test_sleep_wakes_up_early_when_sleepiness_reaches_zero():
    state = _make_base_state()
    # Scheduled for long sleep, but low sleepiness should finish after first interval.
    bot = _sleeping_bot(sleepiness=10, turns_remaining=480, turns_total=480)
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    for _ in range(SLEEP_EFFECT_INTERVAL_TURNS):
        state, _ = tick_zone_map(state)

    bot_state = state["agents"]["bot1"]
    assert bot_state.get("scheduled_action") is None
    completion_entries = v3_action_records(bot_state, "sleep_completed")
    assert completion_entries, "Expected sleep_completed memory after early wake"
    assert (completion_entries[-1].get("details") or {}).get("hours_slept", 0) <= 1.0


def test_interrupted_sleep_keeps_partial_recovery():
    """After 300 ticks (5 hours), partial sleepiness recovery is kept (intervals applied)."""
    from app.games.zone_stalkers.rules.tick_constants import SLEEPINESS_INCREASE_PER_HOUR
    state = _make_base_state()
    bot = _sleeping_bot(sleepiness=100, hunger=10, thirst=10)
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    ticks_5h = 300
    for _ in range(ticks_5h):
        state, _ = tick_zone_map(state)

    bot_state = state["agents"]["bot1"]
    intervals_applied = bot_state["scheduled_action"]["sleep_intervals_applied"]
    assert intervals_applied == ticks_5h // SLEEP_EFFECT_INTERVAL_TURNS

    # Sleepiness is reduced by intervals but also ticks up each in-game hour.
    hours_elapsed = ticks_5h // 60
    interval_recovery = intervals_applied * SLEEPINESS_RECOVERY_PER_SLEEP_INTERVAL
    hourly_increase = hours_elapsed * SLEEPINESS_INCREASE_PER_HOUR
    expected_max = max(0, 100 - interval_recovery + hourly_increase)
    assert bot_state["sleepiness"] <= expected_max + 5  # +5 tolerance for tick ordering


# ─── Intent: critical hunger/thirst vs sleepiness ────────────────────────────

def _run_select_intent(agent_dict: dict, state: dict | None = None):
    agent_id = "bot1"
    if state is None:
        state = make_minimal_state(agent_id=agent_id, agent=agent_dict)
        state["agents"][agent_id] = agent_dict
    ctx = build_agent_context(agent_id, agent_dict, state)
    needs = evaluate_needs(ctx, state)
    return select_intent(ctx, needs, state.get("world_turn", 100))


def test_critical_thirst_beats_sleepiness():
    agent = make_agent(
        hp=100,
        hunger=20,
        thirst=CRITICAL_THIRST_THRESHOLD,
        sleepiness=98,
        money=9000, material_threshold=3000,
        has_weapon=True, has_armor=True, has_ammo=True,
    )
    intent = _run_select_intent(agent)
    assert intent.kind == INTENT_SEEK_WATER, (
        f"Expected INTENT_SEEK_WATER at thirst={CRITICAL_THIRST_THRESHOLD} vs sleepiness=98, got {intent.kind}"
    )


def test_critical_hunger_beats_sleepiness():
    agent = make_agent(
        hp=100,
        hunger=CRITICAL_HUNGER_THRESHOLD,
        thirst=20,
        sleepiness=98,
        money=9000, material_threshold=3000,
        has_weapon=True, has_armor=True, has_ammo=True,
    )
    intent = _run_select_intent(agent)
    assert intent.kind == INTENT_SEEK_FOOD, (
        f"Expected INTENT_SEEK_FOOD at hunger={CRITICAL_HUNGER_THRESHOLD} vs sleepiness=98, got {intent.kind}"
    )


def test_below_critical_thirst_and_sleepiness_high_picks_rest():
    """When thirst is below critical and sleepiness is very high, rest should win."""
    agent = make_agent(
        hp=100,
        hunger=0,
        thirst=CRITICAL_THIRST_THRESHOLD - 1,
        sleepiness=98,
        money=9000, material_threshold=3000,
        has_weapon=True, has_armor=True, has_ammo=True,
    )
    intent = _run_select_intent(agent)
    assert intent.kind == INTENT_REST, (
        f"Expected INTENT_REST when thirst<critical, sleepiness=98, got {intent.kind}"
    )


# ─── Planner: _plan_rest with preparation steps ──────────────────────────────

def _make_rest_intent():
    from app.games.zone_stalkers.decision.models.intent import Intent
    return Intent(kind=INTENT_REST, score=0.9, created_turn=100)


def test_rest_plan_inserts_drink_step_when_thirst_at_safe_threshold():
    agent = make_agent(
        hunger=0,
        thirst=SLEEP_SAFE_THIRST_THRESHOLD,
        sleepiness=80,
        inventory=[{"id": "w1", "type": "water", "value": 0}],
    )
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)
    intent = _make_rest_intent()
    plan = build_plan(ctx, intent, state, 100)

    assert plan.steps[0].kind == STEP_CONSUME_ITEM
    assert plan.steps[0].payload["reason"] == "prepare_sleep_drink"
    assert plan.steps[-1].kind == STEP_SLEEP_FOR_HOURS


def test_rest_plan_inserts_food_step_when_hunger_at_safe_threshold():
    agent = make_agent(
        hunger=SLEEP_SAFE_HUNGER_THRESHOLD,
        thirst=0,
        sleepiness=80,
        inventory=[{"id": "b1", "type": "bread", "value": 0}],
    )
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)
    intent = _make_rest_intent()
    plan = build_plan(ctx, intent, state, 100)

    assert plan.steps[0].kind == STEP_CONSUME_ITEM
    assert plan.steps[0].payload["reason"] == "prepare_sleep_food"
    assert plan.steps[-1].kind == STEP_SLEEP_FOR_HOURS


def test_rest_plan_inserts_both_drink_and_food_before_sleep():
    agent = make_agent(
        hunger=SLEEP_SAFE_HUNGER_THRESHOLD,
        thirst=SLEEP_SAFE_THIRST_THRESHOLD,
        sleepiness=98,
        inventory=[
            {"id": "b1", "type": "bread", "value": 0},
            {"id": "w1", "type": "water", "value": 0},
        ],
    )
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)
    intent = _make_rest_intent()
    plan = build_plan(ctx, intent, state, 100)

    kinds = [s.kind for s in plan.steps]
    reasons = [s.payload.get("reason") for s in plan.steps if s.kind == STEP_CONSUME_ITEM]
    assert "prepare_sleep_drink" in reasons
    assert "prepare_sleep_food" in reasons
    assert plan.steps[-1].kind == STEP_SLEEP_FOR_HOURS


def test_rest_plan_no_preparation_when_below_safe_thresholds():
    agent = make_agent(
        hunger=SLEEP_SAFE_HUNGER_THRESHOLD - 1,
        thirst=SLEEP_SAFE_THIRST_THRESHOLD - 1,
        sleepiness=80,
    )
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)
    intent = _make_rest_intent()
    plan = build_plan(ctx, intent, state, 100)

    assert len(plan.steps) == 1
    assert plan.steps[0].kind == STEP_SLEEP_FOR_HOURS


def test_rest_plan_no_preparation_when_items_not_available():
    """No preparation steps if items not in inventory — sleep goes directly."""
    agent = make_agent(
        hunger=SLEEP_SAFE_HUNGER_THRESHOLD,
        thirst=SLEEP_SAFE_THIRST_THRESHOLD,
        sleepiness=80,
        inventory=[],  # empty inventory
    )
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)
    intent = _make_rest_intent()
    plan = build_plan(ctx, intent, state, 100)

    assert len(plan.steps) == 1
    assert plan.steps[0].kind == STEP_SLEEP_FOR_HOURS


def test_rest_plan_chooses_short_sleep_for_low_sleepiness():
    agent = make_agent(
        hunger=0,
        thirst=0,
        sleepiness=10,
    )
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)
    intent = _make_rest_intent()
    plan = build_plan(ctx, intent, state, 100)

    assert plan.steps[-1].kind == STEP_SLEEP_FOR_HOURS
    assert plan.steps[-1].payload["hours"] == 1


def test_rest_plan_chooses_longer_sleep_for_high_sleepiness():
    from app.games.zone_stalkers.rules.tick_rules import DEFAULT_SLEEP_HOURS
    agent = make_agent(
        hunger=0,
        thirst=0,
        sleepiness=100,
    )
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)
    intent = _make_rest_intent()
    plan = build_plan(ctx, intent, state, 100)

    assert plan.steps[-1].kind == STEP_SLEEP_FOR_HOURS
    assert 1 <= plan.steps[-1].payload["hours"] <= DEFAULT_SLEEP_HOURS
    assert plan.steps[-1].payload["hours"] == DEFAULT_SLEEP_HOURS


# ─── Executor: prepare_sleep_* records correct action_kind ───────────────────

def test_prepare_sleep_drink_records_consume_drink():
    from app.games.zone_stalkers.decision.executors import _exec_consume
    from app.games.zone_stalkers.decision.models.plan import PlanStep

    agent: dict[str, Any] = {
        "inventory": [{"id": "w1", "type": "water", "value": 0, "effects": {"thirst": -30}}],
        "thirst": 75, "hunger": 10, "hp": 80, "max_hp": 100, "radiation": 0,
        "archetype": "stalker_agent",
    }
    state: dict[str, Any] = {
        "world_turn": 100,
        "agents": {"bot1": agent},
        "locations": {},
    }

    from tests.decision.conftest import make_minimal_state
    state = make_minimal_state(agent_id="bot1", agent=agent)
    state["agents"]["bot1"] = agent

    step = PlanStep(
        kind="consume_item",
        payload={"item_type": "water", "reason": "prepare_sleep_drink"},
        interruptible=False,
    )
    ctx = build_agent_context("bot1", agent, state)
    _exec_consume("bot1", agent, step, ctx, state, 100)

    kinds = [
        (r.get("details") or {}).get("action_kind") or r.get("kind")
        for r in v3_records(agent)
    ]
    assert "consume_drink" in kinds, f"Expected consume_drink in {kinds}"


def test_prepare_sleep_food_records_consume_food():
    from app.games.zone_stalkers.decision.executors import _exec_consume
    from app.games.zone_stalkers.decision.models.plan import PlanStep

    agent: dict[str, Any] = {
        "inventory": [{"id": "b1", "type": "bread", "value": 0, "effects": {"hunger": -30}}],
        "thirst": 10, "hunger": 72, "hp": 80, "max_hp": 100, "radiation": 0,
        "archetype": "stalker_agent",
    }
    state = make_minimal_state(agent_id="bot1", agent=agent)
    state["agents"]["bot1"] = agent

    step = PlanStep(
        kind="consume_item",
        payload={"item_type": "bread", "reason": "prepare_sleep_food"},
        interruptible=False,
    )
    ctx = build_agent_context("bot1", agent, state)
    _exec_consume("bot1", agent, step, ctx, state, 100)

    kinds = [
        (r.get("details") or {}).get("action_kind") or r.get("kind")
        for r in v3_records(agent)
    ]
    assert "consume_food" in kinds, f"Expected consume_food in {kinds}"
