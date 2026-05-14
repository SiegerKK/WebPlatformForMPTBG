from __future__ import annotations

from types import SimpleNamespace

from app.games.zone_stalkers.decision.models.agent_context import AgentContext
from app.games.zone_stalkers.decision.models.intent import Intent
from app.games.zone_stalkers.decision.models.plan import STEP_TRAVEL_TO_LOCATION, STEP_WAIT
from app.games.zone_stalkers.decision.objectives.generator import evaluate_kill_target_combat_readiness
from app.games.zone_stalkers.decision.planner import _plan_get_rich, _plan_hunt_target


def test_killer_attacks_visible_target_when_combat_ready() -> None:
    agent = {"hp": 100, "equipment": {"weapon": {"type": "pm"}, "armor": {"type": "jacket"}}}
    target_belief = SimpleNamespace(visible_now=True, co_located=True, combat_strength=0.4)
    need_result = SimpleNamespace(
        combat_readiness={"weapon_missing": 0, "ammo_missing": 0},
        liquidity_summary={"money_missing": 0},
    )

    result = evaluate_kill_target_combat_readiness(
        agent=agent,
        target_belief=target_belief,
        need_result=need_result,
        context=None,
    )

    assert result["combat_ready"] is True
    assert result["should_engage_now"] is True
    assert result["reasons"] == []


def test_killer_delays_attack_when_target_strong_and_no_armor() -> None:
    agent = {"hp": 100, "equipment": {"weapon": {"type": "pm"}, "armor": None}}
    target_belief = SimpleNamespace(visible_now=True, co_located=True, combat_strength=0.95)
    need_result = SimpleNamespace(
        combat_readiness={"weapon_missing": 0, "ammo_missing": 0},
        liquidity_summary={"money_missing": 1200},
    )

    result = evaluate_kill_target_combat_readiness(
        agent=agent,
        target_belief=target_belief,
        need_result=need_result,
        context=None,
    )

    assert result["combat_ready"] is False
    assert "no_armor" in result["reasons"]
    assert "target_too_strong" in result["reasons"]
    assert result["recommended_support_objective"] == "GET_MONEY_FOR_RESUPPLY"


def _agent_context_for_get_rich(agent: dict, locations: dict, current_location: str) -> AgentContext:
    return AgentContext(
        agent_id="npc_1",
        self_state=agent,
        location_state=locations[current_location],
        world_context={},
    )


def test_get_money_for_resupply_avoids_exhausted_location() -> None:
    agent = {
        "id": "npc_1",
        "location_id": "loc_a",
        "inventory": [],
        "equipment": {},
        "risk_tolerance": 0.5,
        "memory_v3": {
            "records": {
                "m1": {
                    "kind": "anomaly_search_exhausted",
                    "created_turn": 90,
                    "location_id": "loc_a",
                    "details": {
                        "action_kind": "anomaly_search_exhausted",
                        "objective_key": "GET_MONEY_FOR_RESUPPLY",
                        "location_id": "loc_a",
                        "cooldown_until_turn": 150,
                    },
                }
            }
        },
    }
    locations = {
        "loc_a": {"id": "loc_a", "anomaly_activity": 7, "connections": [{"to": "loc_b", "travel_time": 10}]},
        "loc_b": {"id": "loc_b", "anomaly_activity": 9, "connections": [{"to": "loc_a", "travel_time": 10}]},
    }
    ctx = _agent_context_for_get_rich(agent, locations, "loc_a")
    intent = Intent(kind="get_rich", score=0.8, metadata={"objective_key": "GET_MONEY_FOR_RESUPPLY"})

    plan = _plan_get_rich(ctx, intent, {"locations": locations}, world_turn=100)

    assert plan is not None
    assert plan.steps[0].kind == STEP_TRAVEL_TO_LOCATION
    assert plan.steps[0].payload["target_id"] == "loc_b"


def test_get_money_for_resupply_waits_when_all_sources_exhausted() -> None:
    agent = {
        "id": "npc_1",
        "location_id": "loc_a",
        "inventory": [],
        "equipment": {},
        "risk_tolerance": 0.5,
        "memory_v3": {
            "records": {
                "m1": {
                    "kind": "anomaly_search_exhausted",
                    "created_turn": 90,
                    "location_id": "loc_a",
                    "details": {
                        "action_kind": "anomaly_search_exhausted",
                        "objective_key": "GET_MONEY_FOR_RESUPPLY",
                        "location_id": "loc_a",
                        "cooldown_until_turn": 150,
                    },
                },
                "m2": {
                    "kind": "anomaly_search_exhausted",
                    "created_turn": 91,
                    "location_id": "loc_b",
                    "details": {
                        "action_kind": "anomaly_search_exhausted",
                        "objective_key": "GET_MONEY_FOR_RESUPPLY",
                        "location_id": "loc_b",
                        "cooldown_until_turn": 150,
                    },
                },
            }
        },
    }
    locations = {
        "loc_a": {"id": "loc_a", "anomaly_activity": 7, "connections": [{"to": "loc_b", "travel_time": 10}]},
        "loc_b": {"id": "loc_b", "anomaly_activity": 9, "connections": [{"to": "loc_a", "travel_time": 10}]},
    }
    ctx = _agent_context_for_get_rich(agent, locations, "loc_a")
    intent = Intent(kind="get_rich", score=0.8, metadata={"objective_key": "GET_MONEY_FOR_RESUPPLY"})

    plan = _plan_get_rich(ctx, intent, {"locations": locations}, world_turn=100)

    assert plan is not None
    assert plan.steps[0].kind == STEP_WAIT
    assert plan.steps[0].payload["reason"] == "get_rich_sources_exhausted"


def test_planner_prefilters_exhausted_witness_source_before_travel() -> None:
    agent = {
        "id": "npc_1",
        "location_id": "loc_a",
        "kill_target_id": "enemy_1",
        "equipment": {"weapon": {"type": "pistol"}},
        "memory_v3": {
            "records": {
                "m1": {
                    "kind": "witness_source_exhausted",
                    "created_turn": 90,
                    "location_id": "loc_b",
                    "details": {
                        "action_kind": "witness_source_exhausted",
                        "objective_key": "TRACK_TARGET",
                        "location_id": "loc_b",
                        "target_id": "enemy_1",
                        "cooldown_until_turn": 150,
                    },
                }
            }
        },
    }
    locations = {
        "loc_a": {"id": "loc_a", "anomaly_activity": 0, "connections": [{"to": "loc_b", "travel_time": 10}]},
        "loc_b": {"id": "loc_b", "anomaly_activity": 0, "connections": [{"to": "loc_a", "travel_time": 10}]},
    }
    ctx = AgentContext(
        agent_id="npc_1",
        self_state=agent,
        location_state=locations["loc_a"],
        world_context={},
    )
    intent = Intent(
        kind="hunt_target",
        score=0.8,
        target_id="enemy_1",
        target_location_id="loc_b",
        metadata={"objective_key": "TRACK_TARGET"},
    )
    plan = _plan_hunt_target(ctx, intent, {"locations": locations, "agents": {}}, world_turn=100)
    assert plan is not None
    assert plan.steps[0].kind != STEP_WAIT
    assert plan.steps[0].kind in {"look_for_tracks", "travel_to_location"}
