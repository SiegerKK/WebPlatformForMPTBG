from __future__ import annotations

from app.games.zone_stalkers.decision.active_plan_manager import create_active_plan
from app.games.zone_stalkers.decision.models.objective import Objective, ObjectiveDecision, ObjectiveScore
from app.games.zone_stalkers.decision.models.plan import Plan, PlanStep, STEP_EXPLORE_LOCATION
from app.games.zone_stalkers.rules.tick_rules import tick_zone_map


def _make_state() -> dict:
    return {
        "seed": 1,
        "world_turn": 100,
        "world_day": 1,
        "world_hour": 12,
        "world_minute": 0,
        "emission_active": False,
        "agents": {},
        "traders": {
            "trader_1": {"id": "trader_1", "location_id": "loc_b", "is_alive": True},
        },
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


def _bot() -> dict:
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
        "hunger": 10,
        "thirst": 10,
        "sleepiness": 10,
        "money": 100,
        "global_goal": "get_rich",
        "material_threshold": 3000,
        "equipment": {"weapon": {"type": "pistol"}, "armor": {"type": "leather_jacket"}},
        "inventory": [{"id": "art1", "type": "soul", "value": 1000}],
        "action_queue": [],
        "scheduled_action": None,
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


def test_tick_writes_brain_v3_context_not_v2_context() -> None:
    state = _make_state()
    bot = _bot()
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)

    new_bot = new_state["agents"]["bot1"]
    assert "brain_v3_context" in new_bot
    assert "_v2_context" not in new_bot
    decision_events = [ev for ev in new_bot["brain_trace"]["events"] if ev.get("mode") == "decision"]
    assert decision_events[-1]["decision"] == "objective_decision"
    assert decision_events[-1]["adapter_intent"]["kind"]


def test_legacy_scheduled_action_is_wrapped_into_active_plan() -> None:
    state = _make_state()
    bot = _bot()
    bot["scheduled_action"] = {
        "type": "travel",
        "turns_remaining": 3,
        "turns_total": 3,
        "target_id": "loc_b",
        "final_target_id": "loc_b",
        "remaining_route": [],
    }
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)

    new_bot = new_state["agents"]["bot1"]
    assert new_bot["active_plan_v3"]["objective_key"] == "LEGACY_RUNTIME_ACTION"
    assert new_bot["scheduled_action"]["active_plan_id"] == new_bot["active_plan_v3"]["id"]
    assert new_bot["action_queue"] == []


def test_active_plan_memory_events_bridge_to_memory_v3() -> None:
    """Updated for Fix 2: active_plan lifecycle noise no longer stored in memory_v3.

    The plan DOES execute (active_plan_v3 and scheduled_action are set), but
    trace-only lifecycle events (active_plan_created, active_plan_step_started)
    are filtered out by the MEMORY_EVENT_POLICY before reaching the store.
    """
    state = _make_state()
    bot = _bot()
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)

    new_bot = new_state["agents"]["bot1"]
    records = list((new_bot.get("memory_v3", {}) or {}).get("records", {}).values())
    kinds = {record.get("kind") for record in records}

    # Lifecycle noise must NOT be stored (Fix 2)
    assert "active_plan_created" not in kinds, (
        "active_plan_created must be filtered by MEMORY_EVENT_POLICY (trace_only)"
    )
    assert "active_plan_step_started" not in kinds, (
        "active_plan_step_started must be filtered by MEMORY_EVENT_POLICY (trace_only)"
    )

    # The plan execution itself still happened
    assert new_bot.get("active_plan_v3") is not None or new_bot.get("scheduled_action") is not None


def test_v3_bot_action_queue_stays_empty_when_active_plan_exists() -> None:
    state = _make_state()
    bot = _bot()
    plan = Plan(intent_kind="explore", steps=[PlanStep(kind=STEP_EXPLORE_LOCATION, payload={"target_id": "loc_a"})])
    active_plan = create_active_plan(_decision(), world_turn=100, plan=plan)
    bot["active_plan_v3"] = active_plan.to_dict()
    bot["action_queue"] = [{"type": "sleep", "turns_remaining": 2, "turns_total": 2}]
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)

    new_bot = new_state["agents"]["bot1"]
    assert new_bot["action_queue"] == []
    assert new_bot["scheduled_action"]["active_plan_id"] == active_plan.id
