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
        "action_queue": [],
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
    bot = _bot_agent()
    bot["action_queue"] = [{"type": "sleep", "turns_remaining": 2, "turns_total": 2, "target_id": "loc_a"}]
    state["agents"]["bot1"] = bot
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


def test_plan_monitor_abort_memory_is_deduplicated_within_window() -> None:
    state = _make_base_state()
    bot = _bot_agent()
    bot["action_queue"] = [{"type": "sleep", "turns_remaining": 2, "turns_total": 2, "target_id": "loc_a"}]
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    state_after_first, _ = tick_zone_map(state)
    bot_after_first = state_after_first["agents"]["bot1"]
    first_count = sum(
        1
        for m in bot_after_first.get("memory", [])
        if m.get("effects", {}).get("action_kind") == "plan_monitor_abort"
    )
    assert first_count == 1

    bot_after_first["scheduled_action"] = {
        "type": "travel",
        "turns_remaining": 4,
        "turns_total": 4,
        "target_id": "loc_b",
        "final_target_id": "loc_b",
        "remaining_route": [],
    }
    bot_after_first["action_queue"] = []

    state_after_second, _ = tick_zone_map(state_after_first)
    bot_after_second = state_after_second["agents"]["bot1"]
    second_count = sum(
        1
        for m in bot_after_second.get("memory", [])
        if m.get("effects", {}).get("action_kind") == "plan_monitor_abort"
    )
    assert second_count == 1


def test_bot_decision_pipeline_writes_decision_brain_trace_event() -> None:
    state = _make_base_state()
    bot = _bot_agent()
    bot["thirst"] = 10
    bot["scheduled_action"] = None
    bot["action_queue"] = []
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)

    trace = new_state["agents"]["bot1"]["brain_trace"]
    assert trace["turn"] == 100
    assert any(ev.get("mode") == "decision" and ev.get("decision") == "new_intent" for ev in trace.get("events", []))


def test_v3_transient_flags_are_removed_after_tick() -> None:
    state = _make_base_state()
    bot = _bot_agent()
    bot["_v3_debug_temp"] = True
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)
    new_bot = new_state["agents"]["bot1"]
    assert not any(k.startswith("_v3_") for k in new_bot.keys())


def test_decision_pipeline_uses_memory_v3_trader_lookup_and_writes_memory_used() -> None:
    from app.games.zone_stalkers.memory.models import MemoryRecord
    from app.games.zone_stalkers.memory.store import add_memory_record

    state = _make_base_state()
    bot = _bot_agent()
    bot["thirst"] = 10
    bot["hunger"] = 10
    bot["scheduled_action"] = None
    bot["action_queue"] = []
    # Artifact in inventory triggers sell_artifacts/get_rich branch.
    bot["inventory"].append({"id": "art1", "type": "soul", "value": 1000})
    # No live traders in state -> planner must rely on memory_v3 fallback.
    state["traders"] = {}

    add_memory_record(
        bot,
        MemoryRecord(
            id="mem_trader_1",
            agent_id="bot1",
            layer="semantic",
            kind="trader_location_known",
            created_turn=90,
            last_accessed_turn=None,
            summary="Торговец в Локации Б",
            details={"trader_id": "trader_1"},
            location_id="loc_b",
            tags=("trader", "trade"),
            confidence=0.9,
            importance=0.8,
        ),
    )

    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)
    bot_new = new_state["agents"]["bot1"]

    # Memory-backed trader lookup should create a travel decision toward loc_b.
    sched = bot_new.get("scheduled_action") or {}
    assert sched.get("target_id") == "loc_b"

    decision_events = [ev for ev in bot_new.get("brain_trace", {}).get("events", []) if ev.get("mode") == "decision"]
    assert decision_events
    mem_used = decision_events[-1].get("memory_used", [])
    assert any(mu.get("used_for") in ("find_trader", "sell_artifacts") for mu in mem_used)


def test_decision_pipeline_uses_memory_v3_water_source_when_no_trader_path() -> None:
    from app.games.zone_stalkers.memory.models import MemoryRecord
    from app.games.zone_stalkers.memory.store import add_memory_record

    state = _make_base_state()
    bot = _bot_agent()
    bot["thirst"] = 60
    bot["hunger"] = 5
    bot["scheduled_action"] = None
    bot["action_queue"] = []
    bot["inventory"] = []  # no water in inventory
    state["traders"] = {}  # no trader path available

    add_memory_record(
        bot,
        MemoryRecord(
            id="mem_water_1",
            agent_id="bot1",
            layer="spatial",
            kind="water_source_known",
            created_turn=95,
            last_accessed_turn=None,
            summary="В Локации Б есть вода",
            details={},
            location_id="loc_b",
            item_types=("water",),
            tags=("water", "drink", "item"),
            confidence=0.8,
            importance=0.7,
        ),
    )

    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)
    bot_new = new_state["agents"]["bot1"]

    sched = bot_new.get("scheduled_action") or {}
    assert sched.get("target_id") == "loc_b"

    decision_events = [ev for ev in bot_new.get("brain_trace", {}).get("events", []) if ev.get("mode") == "decision"]
    assert decision_events
    mem_used = decision_events[-1].get("memory_used", [])
    assert any(mu.get("used_for") == "find_water" for mu in mem_used)
