from __future__ import annotations

from app.games.zone_stalkers.decision.brain_runtime import (
    ensure_brain_runtime,
    should_run_brain,
)
from app.games.zone_stalkers.rules.tick_rules import _add_memory, tick_zone_map


def _base_state() -> dict:
    return {
        "seed": 1,
        "world_turn": 100,
        "world_day": 1,
        "world_hour": 12,
        "world_minute": 0,
        "emission_active": False,
        "emission_scheduled_turn": None,
        "emission_warning_written_turn": None,
        "emission_warning_offset": None,
        "agents": {},
        "traders": {},
        "locations": {
            "loc_a": {
                "name": "A",
                "terrain_type": "buildings",
                "anomaly_activity": 0,
                "connections": [],
                "items": [],
                "agents": [],
            },
        },
        "combat_interactions": {},
        "relations": {},
        "groups": {},
    }


def _bot(agent_id: str) -> dict:
    return {
        "id": agent_id,
        "archetype": "stalker_agent",
        "controller": {"kind": "bot"},
        "name": agent_id,
        "is_alive": True,
        "has_left_zone": False,
        "action_used": False,
        "location_id": "loc_a",
        "hp": 90,
        "max_hp": 100,
        "radiation": 0,
        "hunger": 20,
        "thirst": 20,
        "sleepiness": 10,
        "money": 100,
        "global_goal": "get_rich",
        "equipment": {"weapon": {"type": "pistol"}, "armor": {"type": "leather_jacket"}},
        "inventory": [{"id": "ammo1", "type": "ammo_9mm", "value": 0}],
        "memory": [],
        "action_queue": [],
        "scheduled_action": None,
        "active_plan_v3": None,
        "brain_v3_context": {"objective_key": "IDLE", "intent_kind": "idle"},
    }


def test_brain_skips_when_valid_plan_not_invalidated() -> None:
    agent = _bot("bot1")
    agent["active_plan_v3"] = {"status": "active"}
    runtime = ensure_brain_runtime(agent, 100)
    runtime["valid_until_turn"] = 130
    runtime["invalidated"] = False

    should_run, reason = should_run_brain(agent, 100)

    assert should_run is False
    assert reason == "cached_until_valid"


def test_plan_completed_invalidates_brain() -> None:
    state = _base_state()
    agent = _bot("bot1")
    state["agents"]["bot1"] = agent
    state["locations"]["loc_a"]["agents"] = ["bot1"]
    ensure_brain_runtime(agent, 100)

    _add_memory(
        agent,
        100,
        state,
        "decision",
        "done",
        {"action_kind": "active_plan_completed"},
    )

    br = agent["brain_runtime"]
    assert br["invalidated"] is True
    assert br["invalidators"][-1]["reason"] == "plan_completed"
    assert br["invalidators"][-1]["priority"] == "high"


def test_target_seen_invalidates_and_runs_immediately() -> None:
    state = _base_state()
    state["ai_budget"] = {
        "enabled": True,
        "max_normal_decisions_per_tick": 0,
        "max_background_decisions_per_tick": 0,
        "urgent_decisions_ignore_budget": True,
        "max_decision_delay_turns": 10,
    }
    bot = _bot("bot1")
    ensure_brain_runtime(bot, 100)["valid_until_turn"] = 999
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    _add_memory(
        bot,
        100,
        state,
        "observation",
        "seen",
        {"action_kind": "target_seen", "target_id": "t1", "location_id": "loc_a"},
    )

    new_state, _ = tick_zone_map(state)
    br = new_state["agents"]["bot1"]["brain_runtime"]
    assert br["last_decision_turn"] == 100
    assert br["invalidated"] is False


def test_emission_warning_invalidates_all_exposed_agents() -> None:
    state = _base_state()
    state["emission_scheduled_turn"] = 110
    state["emission_warning_offset"] = 10
    state["agents"] = {"bot1": _bot("bot1"), "bot2": _bot("bot2")}
    state["locations"]["loc_a"]["agents"] = ["bot1", "bot2"]

    new_state, events = tick_zone_map(state)

    assert any(ev.get("event_type") == "emission_warning" for ev in events)
    for agent_id in ("bot1", "bot2"):
        br = new_state["agents"][agent_id]["brain_runtime"]
        assert br["invalidated"] is True
        assert any(inv.get("reason") == "emission_warning_started" for inv in br.get("invalidators", []))


def test_hunter_reacts_to_new_target_intel_even_with_cache() -> None:
    state = _base_state()
    hunter = _bot("hunter")
    hunter["global_goal"] = "kill_stalker"
    hunter["kill_target_id"] = "target_1"
    hunter["active_plan_v3"] = {"status": "active"}
    ensure_brain_runtime(hunter, 100)["valid_until_turn"] = 500
    state["agents"]["hunter"] = hunter
    state["locations"]["loc_a"]["agents"] = ["hunter"]

    _add_memory(
        hunter,
        100,
        state,
        "observation",
        "intel",
        {
            "action_kind": "intel_from_stalker",
            "target_id": "target_1",
            "location_id": "loc_a",
            "observed": "agent_location",
        },
    )

    should_run, reason = should_run_brain(hunter, 100)
    assert should_run is True
    assert reason == "invalidated"
    assert hunter["brain_runtime"]["invalidators"][-1]["reason"] == "target_intel_received"
