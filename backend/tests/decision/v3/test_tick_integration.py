from __future__ import annotations

import pytest

from app.games.zone_stalkers.decision.active_plan_manager import create_active_plan
from app.games.zone_stalkers.decision.models.objective import Objective, ObjectiveDecision, ObjectiveScore
from app.games.zone_stalkers.decision.models.plan import (
    Plan,
    PlanStep,
    STEP_EXPLORE_LOCATION,
    STEP_TRAVEL_TO_LOCATION,
)
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


def _decision(key: str = "FIND_ARTIFACTS") -> ObjectiveDecision:
    return ObjectiveDecision(
        selected=Objective(
            key=key,
            source="test",
            urgency=0.7,
            expected_value=0.8,
            risk=0.1,
            time_cost=0.3,
            resource_cost=0.1,
            confidence=0.9,
            goal_alignment=0.8,
            memory_confidence=0.9,
            reasons=("test",),
            source_refs=("memory:mem_test",),
        ),
        selected_score=ObjectiveScore(
            objective_key=key,
            raw_score=0.8,
            final_score=0.8,
            factors=(),
            penalties=(),
            decision="selected",
        ),
        alternatives=(),
    )


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


def test_v3_active_plan_owns_steps_and_action_queue_stays_empty() -> None:
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
    assert next_sched is None
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


def test_tick_normalizes_oversized_legacy_memory_once_in_memory_prep_path() -> None:
    state = _make_base_state()
    state["legacy_memory_write_enabled"] = False
    bot = _bot_agent()
    bot["memory"] = [
        {
            "world_turn": i,
            "type": "observation",
            "title": f"mem-{i}",
            "summary": f"mem-{i}",
            "effects": {"location_id": "loc_a"},
        }
        for i in range(130)
    ]
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)

    normalized = new_state["agents"]["bot1"]
    assert len(normalized["memory"]) == 100
    assert normalized["memory"][0]["world_turn"] == 30


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
    assert any(ev.get("mode") == "decision" and ev.get("decision") == "objective_decision" for ev in trace.get("events", []))


def test_tick_objective_decision_creates_active_plan_v3() -> None:
    state = _make_base_state()
    bot = _bot_agent()
    bot["thirst"] = 10
    bot["hunger"] = 10
    bot["scheduled_action"] = None
    bot["action_queue"] = []
    bot["inventory"].append({"id": "art1", "type": "soul", "value": 1000})
    state["traders"] = {"trader_1": {"id": "trader_1", "location_id": "loc_b", "is_alive": True}}
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)

    new_bot = new_state["agents"]["bot1"]
    active_plan = new_bot.get("active_plan_v3")
    assert active_plan is not None
    assert active_plan.get("objective_key")
    assert active_plan.get("steps")
    assert new_bot["scheduled_action"]["active_plan_id"] == active_plan["id"]
    assert new_bot["scheduled_action"]["active_plan_step_index"] == 0


def test_tick_scheduled_action_completion_advances_active_plan_step() -> None:
    state = _make_base_state()
    bot = _bot_agent()
    bot["thirst"] = 10
    bot["hunger"] = 10
    plan = Plan(
        intent_kind="explore",
        steps=[
            PlanStep(kind=STEP_TRAVEL_TO_LOCATION, payload={"target_id": "loc_b"}),
            PlanStep(kind=STEP_EXPLORE_LOCATION, payload={"target_id": "loc_b"}),
        ],
    )
    active_plan = create_active_plan(_decision(), world_turn=100, plan=plan)
    bot["active_plan_v3"] = active_plan.to_dict()
    bot["scheduled_action"] = {
        "type": "travel",
        "turns_remaining": 1,
        "turns_total": 1,
        "target_id": "loc_b",
        "final_target_id": "loc_b",
        "remaining_route": [],
        "active_plan_id": active_plan.id,
        "active_plan_step_index": 0,
    }
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)

    new_bot = new_state["agents"]["bot1"]
    new_plan = new_bot["active_plan_v3"]
    assert new_plan["current_step_index"] == 1
    assert new_plan["steps"][0]["status"] == "completed"
    assert new_bot["scheduled_action"]["type"] == "explore_anomaly_location"
    assert new_bot["scheduled_action"]["active_plan_step_index"] == 1
    completed_entries = [
        m
        for m in new_bot.get("memory", [])
        if m.get("effects", {}).get("action_kind") == "active_plan_step_completed"
    ]
    assert completed_entries
    latest_completed = completed_entries[-1]
    assert "шаг 1/2 travel_to_location" in (latest_completed.get("summary") or "")
    effects = latest_completed.get("effects", {})
    assert effects.get("completed_step_index") == 0
    assert effects.get("completed_step_number") == 1
    assert effects.get("completed_step_kind") == "travel_to_location"
    assert effects.get("next_step_index") == 1
    assert effects.get("next_step_kind") == "explore_location"


def test_active_plan_completed_summary_has_no_off_by_one() -> None:
    state = _make_base_state()
    bot = _bot_agent()
    bot["thirst"] = 10
    bot["hunger"] = 10
    plan = Plan(
        intent_kind="explore",
        steps=[PlanStep(kind=STEP_TRAVEL_TO_LOCATION, payload={"target_id": "loc_b"})],
    )
    active_plan = create_active_plan(_decision(), world_turn=100, plan=plan)
    bot["active_plan_v3"] = active_plan.to_dict()
    bot["scheduled_action"] = {
        "type": "travel",
        "turns_remaining": 1,
        "turns_total": 1,
        "target_id": "loc_b",
        "final_target_id": "loc_b",
        "remaining_route": [],
        "active_plan_id": active_plan.id,
        "active_plan_step_index": 0,
    }
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)
    new_bot = new_state["agents"]["bot1"]
    completed_entries = [
        m
        for m in new_bot.get("memory", [])
        if m.get("effects", {}).get("action_kind") == "active_plan_completed"
    ]
    assert completed_entries
    summary = completed_entries[-1].get("summary") or ""
    assert "1/1 steps completed" in summary
    assert "шаг 2/1" not in summary


def test_tick_active_plan_continue_skips_new_objective_decision() -> None:
    state = _make_base_state()
    bot = _bot_agent()
    bot["scheduled_action"] = None
    bot["action_queue"] = []
    bot["thirst"] = 10
    bot["hunger"] = 10
    bot["memory"] = [
        {
            "type": "decision",
            "effects": {"action_kind": "objective_decision", "objective_key": "FIND_ARTIFACTS"},
        }
    ]
    plan = Plan(intent_kind="explore", steps=[PlanStep(kind=STEP_EXPLORE_LOCATION, payload={"target_id": "loc_a"})])
    active_plan = create_active_plan(_decision(), world_turn=100, plan=plan)
    bot["active_plan_v3"] = active_plan.to_dict()
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)

    new_bot = new_state["agents"]["bot1"]
    decision_memories = [
        m
        for m in new_bot.get("memory", [])
        if m.get("type") == "decision"
        and m.get("effects", {}).get("action_kind") == "objective_decision"
    ]
    assert len(decision_memories) == 1
    assert new_bot["active_plan_v3"]["id"] == active_plan.id
    assert new_bot["scheduled_action"]["active_plan_id"] == active_plan.id


def test_tick_emission_interrupt_repairs_active_plan() -> None:
    state = _make_base_state()
    state["emission_active"] = True
    state["emission_ends_turn"] = 200
    state["locations"]["loc_a"]["terrain_type"] = "plain"
    bot = _bot_agent()
    bot["scheduled_action"] = None
    bot["action_queue"] = []
    bot["thirst"] = 10
    bot["hunger"] = 10
    plan = Plan(intent_kind="explore", steps=[PlanStep(kind=STEP_EXPLORE_LOCATION, payload={"target_id": "loc_a"})])
    active_plan = create_active_plan(_decision(), world_turn=100, plan=plan)
    bot["active_plan_v3"] = active_plan.to_dict()
    bot["scheduled_action"] = {
        "type": "explore_anomaly_location",
        "target_id": "loc_a",
        "turns_remaining": 2,
        "turns_total": 2,
        "active_plan_id": active_plan.id,
        "active_plan_step_index": 0,
    }
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)

    new_bot = new_state["agents"]["bot1"]
    new_plan = new_bot["active_plan_v3"]
    assert new_plan["objective_key"] == "FIND_ARTIFACTS"
    assert new_plan["repair_count"] == 1
    assert new_bot["scheduled_action"]["type"] == "travel"
    assert new_bot["scheduled_action"]["active_plan_id"] == active_plan.id
    active_plan_events = [
        ev for ev in new_bot["brain_trace"]["events"]
        if ev.get("mode") == "active_plan"
    ]
    assert any(ev.get("decision") == "active_plan_step_failed" for ev in active_plan_events)
    assert any(ev.get("decision") == "active_plan_repair_requested" for ev in active_plan_events)
    assert any(ev.get("decision") == "active_plan_repaired" for ev in active_plan_events)


def test_tick_monitor_abort_routes_untagged_runtime_into_active_plan_repair() -> None:
    state = _make_base_state()
    state["emission_active"] = True
    state["emission_ends_turn"] = 200
    state["locations"]["loc_a"]["terrain_type"] = "plain"
    bot = _bot_agent()
    bot["thirst"] = 10
    bot["hunger"] = 10
    plan = Plan(intent_kind="explore", steps=[PlanStep(kind=STEP_EXPLORE_LOCATION, payload={"target_id": "loc_a"})])
    active_plan = create_active_plan(_decision(), world_turn=100, plan=plan)
    bot["active_plan_v3"] = active_plan.to_dict()
    bot["scheduled_action"] = {
        "type": "explore_anomaly_location",
        "target_id": "loc_a",
        "turns_remaining": 2,
        "turns_total": 2,
    }
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)

    new_bot = new_state["agents"]["bot1"]
    assert new_bot["active_plan_v3"]["repair_count"] == 1
    assert new_bot["scheduled_action"]["active_plan_id"] == active_plan.id


def test_dead_npc_clears_active_plan_v3() -> None:
    state = _make_base_state()
    state["locations"]["loc_a"]["terrain_type"] = "plain"
    state["emission_scheduled_turn"] = 100
    bot = _bot_agent()
    bot["scheduled_action"] = None
    bot["action_queue"] = []
    bot["thirst"] = 10
    bot["hunger"] = 10
    plan = Plan(intent_kind="explore", steps=[PlanStep(kind=STEP_EXPLORE_LOCATION, payload={"target_id": "loc_a"})])
    active_plan = create_active_plan(_decision(), world_turn=100, plan=plan)
    bot["active_plan_v3"] = active_plan.to_dict()
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)

    new_bot = new_state["agents"]["bot1"]
    assert new_bot["is_alive"] is False
    assert new_bot.get("active_plan_v3") is None
    assert new_bot.get("scheduled_action") is None


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


def test_tick_objective_pipeline_writes_real_objective_trace_fields() -> None:
    state = _make_base_state()
    state["locations"]["loc_b"]["anomaly_activity"] = 12
    bot = _bot_agent()
    bot["scheduled_action"] = None
    bot["action_queue"] = []
    bot["thirst"] = 20
    bot["hunger"] = 20
    bot["sleepiness"] = 0
    bot["money"] = 0
    bot["equipment"]["weapon"] = None
    bot["inventory"] = []
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)
    new_bot = new_state["agents"]["bot1"]

    decision_events = [ev for ev in new_bot.get("brain_trace", {}).get("events", []) if ev.get("mode") == "decision"]
    assert decision_events
    decision_ev = decision_events[-1]

    assert decision_ev.get("active_objective")
    assert decision_ev["active_objective"].get("key") == "GET_MONEY_FOR_RESUPPLY"
    assert decision_ev.get("objective_scores")
    assert decision_ev.get("alternatives") is not None

    ctx = new_bot.get("brain_v3_context", {})
    assert ctx.get("objective_key") == "GET_MONEY_FOR_RESUPPLY"
    active_plan = new_bot.get("active_plan_v3") or {}
    assert active_plan.get("objective_key") == "GET_MONEY_FOR_RESUPPLY"
    assert [step.get("kind") for step in active_plan.get("steps", [])[:2]] == [
        "travel_to_location",
        "explore_location",
    ]


def test_find_artifacts_objective_composes_travel_then_explore() -> None:
    state = _make_base_state()
    state["locations"]["loc_b"]["anomaly_activity"] = 12
    bot = _bot_agent()
    bot["scheduled_action"] = None
    bot["action_queue"] = []
    bot["thirst"] = 10
    bot["hunger"] = 10
    bot["sleepiness"] = 0
    bot["money"] = 5000
    bot["material_threshold"] = 3000
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)
    new_bot = new_state["agents"]["bot1"]
    active_plan = new_bot.get("active_plan_v3") or {}
    if active_plan.get("objective_key") != "FIND_ARTIFACTS":
        pytest.skip("Scenario selected a different strategic objective.")
    assert [step.get("kind") for step in active_plan.get("steps", [])[:2]] == [
        "travel_to_location",
        "explore_location",
    ]


def test_tick_decision_memory_is_objective_first_and_updates_current_goal() -> None:
    state = _make_base_state()
    state["locations"]["loc_b"]["anomaly_activity"] = 12
    bot = _bot_agent()
    bot["scheduled_action"] = None
    bot["action_queue"] = []
    bot["thirst"] = 15
    bot["hunger"] = 29
    bot["sleepiness"] = 0
    bot["money"] = 0
    bot["equipment"]["weapon"] = None
    bot["inventory"] = []
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)
    new_bot = new_state["agents"]["bot1"]

    decision_memories = [
        m
        for m in new_bot.get("memory", [])
        if m.get("type") == "decision"
        and m.get("effects", {}).get("action_kind") == "objective_decision"
    ]
    assert decision_memories
    effects = decision_memories[-1]["effects"]
    assert effects.get("objective_key") == "GET_MONEY_FOR_RESUPPLY"
    assert effects.get("adapter_intent_kind")
    assert new_bot.get("current_goal") == "get_money_for_resupply"


def test_wait_only_restore_food_plan_falls_back_to_next_objective(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.games.zone_stalkers.decision import planner as planner_module
    from app.games.zone_stalkers.decision.models.plan import Plan, PlanStep, STEP_WAIT

    original_build_plan = planner_module.build_plan

    def _patched_build_plan(ctx, intent, state, world_turn, need_result=None):
        if intent.kind == "seek_food":
            return Plan(
                intent_kind=intent.kind,
                steps=[PlanStep(STEP_WAIT, {"reason": "forced_wait"})],
                confidence=0.2,
                created_turn=world_turn,
            )
        return original_build_plan(ctx, intent, state, world_turn, need_result=need_result)

    monkeypatch.setattr(planner_module, "build_plan", _patched_build_plan)

    state = _make_base_state()
    bot = _bot_agent()
    bot["scheduled_action"] = None
    bot["action_queue"] = []
    bot["hunger"] = 55
    bot["thirst"] = 5
    bot["money"] = 0
    bot["equipment"]["weapon"] = None
    bot["inventory"] = []
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)
    new_bot = new_state["agents"]["bot1"]
    decision_events = [ev for ev in new_bot.get("brain_trace", {}).get("events", []) if ev.get("mode") == "decision"]
    assert decision_events
    decision_ev = decision_events[-1]

    assert decision_ev.get("active_objective", {}).get("key") != "RESTORE_FOOD"
    assert any(
        item.get("key") == "RESTORE_FOOD" and "plan_unavailable" in (item.get("reason") or "")
        for item in decision_ev.get("objective_scores", [])
    )


def test_material_threshold_does_not_complete_get_rich_goal() -> None:
    state = _make_base_state()
    bot = _bot_agent()
    bot["scheduled_action"] = None
    bot["action_queue"] = []
    bot["money"] = 6599
    bot["material_threshold"] = 5477
    bot["wealth_goal_target"] = 88060
    bot["global_goal"] = "get_rich"
    bot["global_goal_achieved"] = False
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)
    new_bot = new_state["agents"]["bot1"]

    assert new_bot.get("global_goal_achieved") is not True
    completion_entries = [
        m
        for m in new_bot.get("memory", [])
        if m.get("effects", {}).get("action_kind") == "global_goal_completed"
    ]
    assert not completion_entries
    decision_events = [ev for ev in new_bot.get("brain_trace", {}).get("events", []) if ev.get("mode") == "decision"]
    assert decision_events
    assert decision_events[-1].get("active_objective", {}).get("key") != "LEAVE_ZONE"


def test_get_rich_liquid_wealth_completion_generates_leave_zone_objective() -> None:
    state = _make_base_state()
    state["locations"]["loc_b"]["exit_zone"] = True
    bot = _bot_agent()
    bot["scheduled_action"] = None
    bot["action_queue"] = []
    bot["money"] = 100
    bot["thirst"] = 10
    bot["hunger"] = 10
    bot["sleepiness"] = 0
    bot["material_threshold"] = 5477
    bot["wealth_goal_target"] = 2000
    bot["global_goal"] = "get_rich"
    bot["global_goal_achieved"] = False
    bot["inventory"].append({"id": "art_goal", "type": "soul", "value": 2500})
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)
    new_bot = new_state["agents"]["bot1"]

    assert new_bot.get("global_goal_achieved") is True
    completion_entries = [
        m
        for m in new_bot.get("memory", [])
        if m.get("effects", {}).get("action_kind") == "global_goal_completed"
    ]
    assert completion_entries
    completion_effects = completion_entries[-1].get("effects", {})
    assert completion_effects.get("global_goal") == "get_rich"
    assert completion_effects.get("liquid_wealth", 0) >= completion_effects.get("wealth_goal_target", 0)

    decision_events = [ev for ev in new_bot.get("brain_trace", {}).get("events", []) if ev.get("mode") == "decision"]
    assert decision_events
    assert decision_events[-1].get("active_objective", {}).get("key") == "LEAVE_ZONE"
    active_plan = new_bot.get("active_plan_v3") or {}
    assert active_plan.get("objective_key") == "LEAVE_ZONE"
