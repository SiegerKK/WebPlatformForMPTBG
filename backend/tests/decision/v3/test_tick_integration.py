from __future__ import annotations

from app.games.zone_stalkers.rules.tick_rules import tick_zone_map


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


def _bot_agent() -> dict:
    return {
        "archetype": "stalker_agent",
        "controller": {"kind": "bot"},
        "name": "bot",
        "is_alive": True,
        "has_left_zone": False,
        "action_used": False,
        "location_id": "loc_a",
        "hp": 90,
        "max_hp": 100,
        "radiation": 0,
        "hunger": 20,
        "thirst": 96,
        "sleepiness": 10,
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
        "action_queue": [{"type": "sleep", "turns_remaining": 2, "turns_total": 2, "target_id": "loc_a"}],
        "scheduled_action": {
            "type": "travel",
            "turns_remaining": 5,
            "turns_total": 5,
            "target_id": "loc_b",
            "final_target_id": "loc_b",
            "remaining_route": [],
        },
    }


def test_plan_monitor_abort_emits_event_and_clears_action_queue() -> None:
    state = _make_base_state()
    state["agents"]["bot1"] = _bot_agent()
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, events = tick_zone_map(state)

    bot = new_state["agents"]["bot1"]
    abort_events = [e for e in events if e.get("event_type") == "plan_monitor_aborted_action"]
    assert abort_events
    assert bot.get("scheduled_action") is None
    assert bot.get("action_queue") == []
    assert bot.get("brain_trace") is not None
    assert bot["brain_trace"]["turn"] == 100


def test_human_agent_not_monitored_by_plan_monitor() -> None:
    state = _make_base_state()
    human = _bot_agent()
    human["controller"] = {"kind": "human"}
    human["thirst"] = 99
    state["agents"]["human1"] = human
    state["locations"]["loc_a"]["agents"] = ["human1"]

    _, events = tick_zone_map(state)

    abort_events = [e for e in events if e.get("event_type") == "plan_monitor_aborted_action"]
    assert not abort_events


def test_emergency_flee_is_not_aborted_by_plan_monitor() -> None:
    state = _make_base_state()
    bot = _bot_agent()
    bot["scheduled_action"]["emergency_flee"] = True
    bot["scheduled_action"]["turns_remaining"] = 2
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, events = tick_zone_map(state)

    abort_events = [e for e in events if e.get("event_type") == "plan_monitor_aborted_action"]
    assert not abort_events
    assert new_state["agents"]["bot1"].get("scheduled_action") is not None


def test_continue_path_keeps_legacy_action_queue_progression() -> None:
    state = _make_base_state()
    bot = _bot_agent()
    bot["thirst"] = 10
    bot["scheduled_action"] = {
        "type": "sleep",
        "turns_remaining": 1,
        "turns_total": 1,
        "target_id": "loc_a",
    }
    bot["action_queue"] = [{
        "type": "sleep",
        "turns_remaining": 2,
        "turns_total": 2,
        "target_id": "loc_a",
    }]
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)

    next_sched = new_state["agents"]["bot1"].get("scheduled_action")
    assert next_sched is not None
    assert next_sched.get("turns_remaining") == 2
    assert new_state["agents"]["bot1"].get("action_queue") == []
