"""Tests for emission sleep interrupt and death cleanup (targeted fixes).

Covers:
  Patch 1 — PlanMonitor aborts sleep when emission_imminent memory exists.
  Patch 1 — PlanMonitor aborts sleep when emission_active is true.
  Patch 1 — Emergency flee is NOT interrupted by emission threat.
  Patch 2 — Emission death clears scheduled_action and action_queue.
  Patch 2 — Starvation/thirst death clears scheduled_action and action_queue.
  Patch 3 — Brain trace does not remain "continue sleep" after death.
"""
from __future__ import annotations

import copy

import pytest

from app.games.zone_stalkers.rules.tick_rules import tick_zone_map


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _plain_location(loc_id: str, connections: list | None = None) -> dict:
    return {
        "name": loc_id,
        "terrain_type": "plain",
        "anomaly_activity": 0,
        "connections": connections or [],
        "items": [],
        "agents": [loc_id],
    }


def _shelter_location(loc_id: str, connections: list | None = None) -> dict:
    return {
        "name": loc_id,
        "terrain_type": "buildings",
        "anomaly_activity": 0,
        "connections": connections or [],
        "items": [],
        "agents": [loc_id],
    }


def _sleeping_bot(bot_id: str, location_id: str, hunger: int = 20, thirst: int = 20) -> dict:
    return {
        "archetype": "stalker_agent",
        "controller": {"kind": "bot"},
        "name": bot_id,
        "is_alive": True,
        "has_left_zone": False,
        "action_used": False,
        "location_id": location_id,
        "hp": 90,
        "max_hp": 100,
        "radiation": 0,
        "hunger": hunger,
        "thirst": thirst,
        "sleepiness": 50,
        "money": 100,
        "global_goal": "get_rich",
        "material_threshold": 3000,
        "equipment": {"weapon": {"type": "pistol"}, "armor": {"type": "leather_jacket"}},
        "inventory": [
            {"id": "ammo1", "type": "ammo_9mm", "value": 0},
            {"id": "food1", "type": "bread", "value": 0},
            {"id": "food2", "type": "bread", "value": 0},
            {"id": "water1", "type": "water", "value": 0},
            {"id": "water2", "type": "water", "value": 0},
            {"id": "med1", "type": "bandage", "value": 0},
            {"id": "med2", "type": "bandage", "value": 0},
            {"id": "med3", "type": "bandage", "value": 0},
        ],
        "memory": [],
        "action_queue": [],
        "scheduled_action": {
            "type": "sleep",
            "hours": 8,
            "turns_remaining": 200,
            "turns_total": 480,
            "sleep_progress_turns": 0,
            "sleep_intervals_applied": 0,
            "sleep_turns_slept": 0,
        },
    }


def _add_emission_imminent_memory(bot: dict, world_turn: int, turns_until: int = 10) -> None:
    """Add an emission_imminent observation to the bot's memory."""
    bot["memory"].append({
        "world_turn": world_turn,
        "type": "observation",
        "title": "⚠️ Скоро выброс!",
        "effects": {
            "action_kind": "emission_imminent",
            "turns_until": turns_until,
            "emission_scheduled_turn": world_turn + turns_until,
        },
        "summary": f"Скоро выброс через {turns_until} ходов",
    })


def _base_state(world_turn: int = 100) -> dict:
    return {
        "seed": 1,
        "world_turn": world_turn,
        "world_day": 1,
        "world_hour": 12,
        "world_minute": 0,
        "emission_active": False,
        "agents": {},
        "traders": {},
        "locations": {},
        "combat_interactions": {},
        "relations": {},
        "groups": {},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Patch 1 — PlanMonitor aborts sleep on emission threat
# ─────────────────────────────────────────────────────────────────────────────

def test_sleep_is_interrupted_by_emission_imminent_memory() -> None:
    """Sleeping bot with emission_imminent memory → scheduled_action is cleared."""
    bot_id = "bot1"
    state = _base_state(world_turn=100)
    bot = _sleeping_bot(bot_id, location_id="loc_a")
    _add_emission_imminent_memory(bot, world_turn=100, turns_until=10)
    state["agents"][bot_id] = bot
    state["locations"]["loc_a"] = _shelter_location(
        "loc_a",
        connections=[{"to": "loc_b", "travel_time": 12}],
    )
    state["locations"]["loc_b"] = _shelter_location(
        "loc_b",
        connections=[{"to": "loc_a", "travel_time": 12}],
    )

    new_state, events = tick_zone_map(state)

    bot_after = new_state["agents"][bot_id]
    assert bot_after["scheduled_action"] is not None, (
        "Sleep interrupt should be routed into ActivePlan repair/runtime continuation"
    )
    assert bot_after["scheduled_action"]["active_plan_id"] == bot_after["active_plan_v3"]["id"]
    # PlanMonitor abort event should be emitted
    abort_events = [e for e in events if e.get("event_type") == "plan_monitor_aborted_action"]
    assert abort_events, "plan_monitor_aborted_action event must be emitted on sleep interrupt"
    assert abort_events[0]["payload"]["reason"] == "emission_threat"
    assert abort_events[0]["payload"]["scheduled_action_type"] == "sleep"


def test_sleep_is_interrupted_when_emission_active() -> None:
    """Sleeping bot with active emission (state["emission_active"]=True) → sleep cleared."""
    bot_id = "bot1"
    state = _base_state(world_turn=100)
    state["emission_active"] = True
    state["emission_ends_turn"] = 150
    bot = _sleeping_bot(bot_id, location_id="loc_a")
    state["agents"][bot_id] = bot
    state["locations"]["loc_a"] = _shelter_location(
        "loc_a",
        connections=[{"to": "loc_b", "travel_time": 12}],
    )
    state["locations"]["loc_b"] = _shelter_location(
        "loc_b",
        connections=[{"to": "loc_a", "travel_time": 12}],
    )

    new_state, events = tick_zone_map(state)

    bot_after = new_state["agents"][bot_id]
    assert bot_after["scheduled_action"] is not None, (
        "Sleep interrupt should be routed into ActivePlan repair/runtime continuation"
    )
    assert bot_after["scheduled_action"]["active_plan_id"] == bot_after["active_plan_v3"]["id"]
    abort_events = [e for e in events if e.get("event_type") == "plan_monitor_aborted_action"]
    assert abort_events
    assert abort_events[0]["payload"]["reason"] == "emission_threat"


def test_brain_trace_records_emission_threat_on_sleep_interrupt() -> None:
    """After sleep interrupt by emission, brain_trace must mention emission_threat."""
    bot_id = "bot1"
    state = _base_state(world_turn=100)
    bot = _sleeping_bot(bot_id, location_id="loc_a")
    _add_emission_imminent_memory(bot, world_turn=100, turns_until=10)
    state["agents"][bot_id] = bot
    state["locations"]["loc_a"] = _shelter_location(
        "loc_a",
        connections=[{"to": "loc_b", "travel_time": 12}],
    )
    state["locations"]["loc_b"] = _shelter_location(
        "loc_b",
        connections=[{"to": "loc_a", "travel_time": 12}],
    )

    new_state, _events = tick_zone_map(state)

    bot_after = new_state["agents"][bot_id]
    trace = bot_after.get("brain_trace")
    assert trace is not None, "brain_trace should exist after abort"
    # The abort event may not be the last one (the decision pipeline runs after abort),
    # so check that at least one event records emission_threat.
    emission_events = [e for e in trace["events"] if e.get("reason") == "emission_threat"]
    assert emission_events, (
        f"brain_trace must contain an event with reason=emission_threat; "
        f"got events: {[e.get('reason') for e in trace['events']]}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Patch 1 — Emergency flee is NOT interrupted by emission threat
# ─────────────────────────────────────────────────────────────────────────────

def test_emergency_flee_not_interrupted_by_emission_warning() -> None:
    """Bot on an emergency flee travel → must NOT be interrupted even with emission threat."""
    bot_id = "bot1"
    state = _base_state(world_turn=100)
    bot = _sleeping_bot(bot_id, location_id="loc_a")
    # Override scheduled_action to emergency flee travel (not sleep)
    bot["scheduled_action"] = {
        "type": "travel",
        "target_id": "loc_b",
        "final_target_id": "loc_b",
        "remaining_route": [],
        "turns_remaining": 5,
        "turns_total": 10,
        "emergency_flee": True,
    }
    _add_emission_imminent_memory(bot, world_turn=100, turns_until=10)
    state["agents"][bot_id] = bot
    state["locations"]["loc_a"] = _shelter_location(
        "loc_a",
        connections=[{"to": "loc_b", "travel_time": 12}],
    )
    state["locations"]["loc_b"] = _shelter_location(
        "loc_b",
        connections=[{"to": "loc_a", "travel_time": 12}],
    )

    new_state, events = tick_zone_map(state)

    bot_after = new_state["agents"][bot_id]
    # Emergency flee must not be interrupted — either still traveling or reached destination
    abort_events = [
        e for e in events
        if e.get("event_type") == "plan_monitor_aborted_action"
        and e["payload"].get("agent_id") == bot_id
    ]
    assert not abort_events, (
        "Emergency flee must NOT be interrupted by emission warning"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Patch 2 — Death cleanup: scheduled_action/action_queue cleared
# ─────────────────────────────────────────────────────────────────────────────

def test_emission_death_clears_scheduled_action() -> None:
    """Agent on dangerous terrain with active emission → dies + scheduled_action cleared."""
    bot_id = "bot1"
    state = _base_state(world_turn=100)
    # Schedule emission to fire this exact tick
    state["emission_scheduled_turn"] = 100
    bot = _sleeping_bot(bot_id, location_id="loc_plain")
    bot["action_queue"] = [{"type": "sleep", "hours": 4}]  # also test queue is cleared
    state["agents"][bot_id] = bot
    state["locations"]["loc_plain"] = {
        "name": "Открытая равнина",
        "terrain_type": "plain",
        "anomaly_activity": 0,
        "connections": [],
        "items": [],
        "agents": [bot_id],
    }

    new_state, events = tick_zone_map(state)

    bot_after = new_state["agents"][bot_id]
    assert bot_after["is_alive"] is False, "Agent on plain during emission must die"
    assert bot_after["scheduled_action"] is None, (
        "scheduled_action must be None after death"
    )
    assert bot_after.get("action_queue", []) == [], (
        "action_queue must be empty after death"
    )
    died_events = [e for e in events if e.get("event_type") == "agent_died"
                   and e["payload"]["agent_id"] == bot_id]
    assert died_events, "agent_died event must be emitted"
    assert died_events[0]["payload"]["cause"] == "emission"


def test_starvation_death_clears_scheduled_action() -> None:
    """Agent dying from hunger (hp drops to 0 on hour boundary) → scheduled_action cleared."""
    bot_id = "bot1"
    state = _base_state(world_turn=100)
    # Set minute=59 so that the hour boundary triggers on this tick
    state["world_minute"] = 59
    bot = _sleeping_bot(bot_id, location_id="loc_a", hunger=100, thirst=100)
    bot["hp"] = 1  # will die from critical hunger/thirst damage this tick
    bot["action_queue"] = [{"type": "sleep", "hours": 4}]
    state["agents"][bot_id] = bot
    state["locations"]["loc_a"] = _shelter_location(
        "loc_a",
        connections=[{"to": "loc_b", "travel_time": 12}],
    )
    state["locations"]["loc_b"] = _shelter_location(
        "loc_b",
        connections=[{"to": "loc_a", "travel_time": 12}],
    )

    new_state, events = tick_zone_map(state)

    bot_after = new_state["agents"][bot_id]
    assert bot_after["is_alive"] is False, "Agent with hp=1 and critical hunger/thirst must die"
    assert bot_after["scheduled_action"] is None, (
        "scheduled_action must be None after starvation death"
    )
    assert bot_after.get("action_queue", []) == [], (
        "action_queue must be empty after death"
    )
    died_events = [e for e in events if e.get("event_type") == "agent_died"
                   and e["payload"]["agent_id"] == bot_id]
    assert died_events
    assert died_events[0]["payload"]["cause"] == "starvation_or_thirst"


# ─────────────────────────────────────────────────────────────────────────────
# Patch 3 — Brain trace not "continue sleep" after death
# ─────────────────────────────────────────────────────────────────────────────

def test_emission_death_updates_brain_trace() -> None:
    """After emission death, brain_trace must not claim continue sleep."""
    bot_id = "bot1"
    state = _base_state(world_turn=100)
    state["emission_scheduled_turn"] = 100
    bot = _sleeping_bot(bot_id, location_id="loc_plain")
    state["agents"][bot_id] = bot
    state["locations"]["loc_plain"] = {
        "name": "Открытая равнина",
        "terrain_type": "plain",
        "anomaly_activity": 0,
        "connections": [],
        "items": [],
        "agents": [bot_id],
    }

    new_state, _events = tick_zone_map(state)

    bot_after = new_state["agents"][bot_id]
    assert bot_after["is_alive"] is False
    trace = bot_after.get("brain_trace")
    if trace:
        current_thought = trace.get("current_thought", "")
        assert "sleep" not in current_thought.lower() or "погиб" in current_thought.lower(), (
            f"brain_trace must not claim 'continue sleep' after death; got: {current_thought!r}"
        )
        assert "Погиб" in current_thought or trace.get("mode") == "system", (
            f"brain_trace must indicate death; got: {current_thought!r}"
        )


def test_starvation_death_updates_brain_trace() -> None:
    """After starvation death, brain_trace must indicate agent died."""
    bot_id = "bot1"
    state = _base_state(world_turn=100)
    state["world_minute"] = 59
    bot = _sleeping_bot(bot_id, location_id="loc_a", hunger=100, thirst=100)
    bot["hp"] = 1
    state["agents"][bot_id] = bot
    state["locations"]["loc_a"] = _shelter_location(
        "loc_a",
        connections=[{"to": "loc_b", "travel_time": 12}],
    )
    state["locations"]["loc_b"] = _shelter_location(
        "loc_b",
        connections=[{"to": "loc_a", "travel_time": 12}],
    )

    new_state, _events = tick_zone_map(state)

    bot_after = new_state["agents"][bot_id]
    assert bot_after["is_alive"] is False
    trace = bot_after.get("brain_trace")
    if trace:
        current_thought = trace.get("current_thought", "")
        # Must mention death, not ongoing sleep
        assert "Погиб" in current_thought or trace.get("mode") == "system", (
            f"brain_trace must indicate death; got: {current_thought!r}"
        )
