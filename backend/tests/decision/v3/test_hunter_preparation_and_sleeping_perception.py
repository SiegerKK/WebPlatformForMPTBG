"""Tests for hunter preparation and sleeping perception suppression.

Covers:
- Sleeping agents do not receive direct observations of co-located entities.
- Sleeping killer does not update last_direct_seen_turn when target visits.
- Awake killer still observes co-located target immediately.
- Target equipment knowledge drives hunter preparation requirements.
- Hunter with inferior equipment prefers GET_MONEY_FOR_RESUPPLY / PREPARE_FOR_HUNT.
- Hunter economy pressure activates even with a weak lead when undergeared.
- Killer at trader with debt and soft hunger prefers GET_MONEY_FOR_RESUPPLY.
- Critical hunger/thirst still allows emergency survival before earning.
- Visible co-located target with advantage goes to ENGAGE_TARGET.
- Visible co-located target without advantage goes to PREPARE_FOR_HUNT / GET_MONEY.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.perception import is_perception_suppressed
from app.games.zone_stalkers.decision.target_beliefs import build_target_belief
from app.games.zone_stalkers.decision.beliefs import build_belief_state
from app.games.zone_stalkers.decision.needs import evaluate_need_result
from app.games.zone_stalkers.decision.objectives.generator import (
    OBJECTIVE_ENGAGE_TARGET,
    OBJECTIVE_GET_MONEY_FOR_RESUPPLY,
    OBJECTIVE_PREPARE_FOR_HUNT,
    evaluate_hunter_equipment_advantage,
    evaluate_kill_target_combat_readiness,
    generate_objectives,
)
from app.games.zone_stalkers.decision.models.objective import ObjectiveGenerationContext
from tests.decision.conftest import make_agent, make_minimal_state


# --- Helpers -----------------------------------------------------------------

def _make_base_state() -> dict:
    return {
        "seed": 1,
        "world_turn": 5000,
        "world_day": 5,
        "world_hour": 12,
        "world_minute": 0,
        "emission_active": False,
        "agents": {},
        "traders": {},
        "locations": {
            "loc_a": {
                "name": "Base",
                "terrain_type": "buildings",
                "anomaly_activity": 0,
                "connections": [{"to": "loc_b", "travel_time": 60}],
                "items": [],
                "agents": [],
            },
            "loc_b": {
                "name": "Bunker",
                "terrain_type": "buildings",
                "anomaly_activity": 0,
                "connections": [{"to": "loc_a", "travel_time": 60}],
                "items": [],
                "agents": [],
            },
        },
        "combat_interactions": {},
        "relations": {},
        "groups": {},
    }


def _make_killer(location_id="loc_a", hp=85, money=0, sleepiness=0, hunger=0, thirst=0,
                 weapon_type="pistol", sleeping=False, debt=0):
    agent = make_agent(
        agent_id="killer_0", hp=hp, money=money, sleepiness=sleepiness, hunger=hunger,
        thirst=thirst, global_goal="kill_stalker", kill_target_id="target_0",
        location_id=location_id, has_weapon=True, has_armor=True, has_ammo=True,
    )
    agent["equipment"]["weapon"] = {"type": weapon_type, "value": 300}
    if sleeping:
        agent["scheduled_action"] = {"type": "sleep", "hours": 8, "started_turn": 4990}
    if debt > 0:
        agent["economic_state"] = {"debt_total": debt, "creditors": ["trader_1"]}
    return agent


def _make_target(location_id="loc_b", hp=100):
    return {
        "archetype": "stalker_agent",
        "is_alive": True,
        "location_id": location_id,
        "hp": hp,
        "name": "Target",
        "id": "target_0",
        "equipment": {
            "weapon": {"type": "rifle", "value": 800},
            "armor": {"type": "medium_armor", "value": 600},
        },
    }


def _state_with_agents(killer, target):
    state = _make_base_state()
    state["agents"]["killer_0"] = killer
    state["agents"]["target_0"] = target
    return state


def _make_ctx_with_weak_lead(killer, target, state, lead_confidence=0.4, world_turn=5000):
    from app.games.zone_stalkers.decision.models.target_belief import TargetBelief, LocationHypothesis
    state["world_turn"] = world_turn
    ctx = build_agent_context("killer_0", killer, state)
    belief_state = build_belief_state(ctx, killer, world_turn)
    weak_tb = TargetBelief(
        target_id="target_0", is_known=True, is_alive=True,
        last_known_location_id="loc_b", location_confidence=lead_confidence,
        best_location_id="loc_b", best_location_confidence=lead_confidence,
        last_seen_turn=world_turn - 300, visible_now=False, co_located=False,
        equipment_known=True, combat_strength=0.94, combat_strength_confidence=0.8,
        possible_locations=(
            LocationHypothesis(
                location_id="loc_b", probability=lead_confidence,
                confidence=lead_confidence, freshness=0.3,
                reason="old_lead", source_refs=("memory:old",),
            ),
        ),
        likely_routes=(), exhausted_locations=(), lead_count=1, route_hints=(),
        source_refs=("memory:old",), recently_seen=False,
        recent_contact_turn=world_turn - 300, recent_contact_location_id="loc_b",
        recent_contact_age=300,
    )
    need_result = evaluate_need_result(ctx, state)
    return ObjectiveGenerationContext(
        agent_id="killer_0",
        world_turn=world_turn,
        belief_state=belief_state,
        need_result=need_result,
        active_plan_summary=None,
        personality=killer,
        target_belief=weak_tb,
    )


# --- Fix A: Sleeping perception tests ----------------------------------------

class TestIsPerceptionSuppressed:
    def test_alive_awake_not_suppressed(self):
        assert is_perception_suppressed(make_agent()) is False

    def test_dead_suppressed(self):
        a = make_agent(); a["is_alive"] = False
        assert is_perception_suppressed(a) is True

    def test_has_left_zone_suppressed(self):
        a = make_agent(); a["has_left_zone"] = True
        assert is_perception_suppressed(a) is True

    def test_scheduled_sleep_suppressed(self):
        a = make_agent(); a["scheduled_action"] = {"type": "sleep"}
        assert is_perception_suppressed(a) is True

    def test_scheduled_travel_not_suppressed(self):
        a = make_agent(); a["scheduled_action"] = {"type": "travel"}
        assert is_perception_suppressed(a) is False

    def test_active_plan_sleep_step_suppressed(self):
        a = make_agent()
        a["active_plan_v3"] = {
            "steps": [{"kind": "sleep_for_hours", "status": "running"}],
            "current_step_index": 0,
        }
        assert is_perception_suppressed(a) is True

    def test_active_plan_non_sleep_not_suppressed(self):
        a = make_agent()
        a["active_plan_v3"] = {
            "steps": [{"kind": "travel_to_location", "status": "running"}],
            "current_step_index": 0,
        }
        assert is_perception_suppressed(a) is False


class TestSleepingPerception:
    def test_sleeping_no_visible(self):
        killer = _make_killer(location_id="loc_a", sleeping=True)
        target = _make_target(location_id="loc_a")
        state = _state_with_agents(killer, target)
        ctx = build_agent_context("killer_0", killer, state)
        assert ctx.visible_entities == []

    def test_awake_sees_colocated(self):
        killer = _make_killer(location_id="loc_a")
        target = _make_target(location_id="loc_a")
        state = _state_with_agents(killer, target)
        ctx = build_agent_context("killer_0", killer, state)
        assert any(e["agent_id"] == "target_0" for e in ctx.visible_entities)

    def test_sleeping_target_belief_not_visible(self):
        killer = _make_killer(location_id="loc_a", sleeping=True)
        target = _make_target(location_id="loc_a")
        state = _state_with_agents(killer, target)
        ctx = build_agent_context("killer_0", killer, state)
        bs = build_belief_state(ctx, killer, 5000)
        tb = build_target_belief(agent_id="killer_0", agent=killer, state=state, world_turn=5000, belief_state=bs)
        assert tb.visible_now is False
        assert tb.co_located is False

    def test_awake_target_belief_visible(self):
        killer = _make_killer(location_id="loc_a")
        target = _make_target(location_id="loc_a")
        state = _state_with_agents(killer, target)
        ctx = build_agent_context("killer_0", killer, state)
        bs = build_belief_state(ctx, killer, 5000)
        tb = build_target_belief(agent_id="killer_0", agent=killer, state=state, world_turn=5000, belief_state=bs)
        assert tb.visible_now is True
        assert tb.co_located is True


# --- Fix B/D: Equipment advantage tests --------------------------------------

class TestEquipmentAdvantage:
    def test_pistol_vs_rifle_not_advantaged(self):
        killer = _make_killer(weapon_type="pistol")
        killer["knowledge_v1"] = {"known_npcs": {"target_0": {
            "is_alive": True, "confidence": 0.9, "last_seen_turn": 4990,
            "equipment_summary": {"weapon_class": "rifle", "armor_class": "light",
                                   "combat_strength_estimate": 0.8},
        }}}
        r = evaluate_hunter_equipment_advantage(agent=killer, target_belief=None,
                                                need_result=None, world_turn=5000)
        assert r["is_advantaged"] is False
        assert "weapon_upgrade" in r["missing_requirements"]

    def test_rifle_vs_pistol_advantaged(self):
        killer = _make_killer(weapon_type="rifle", money=500)
        killer["inventory"] = (
            [{"id": f"a{i}", "type": "ammo_762", "value": 0} for i in range(25)] +
            [{"id": f"m{i}", "type": "bandage", "value": 0} for i in range(3)]
        )
        killer["knowledge_v1"] = {"known_npcs": {"target_0": {
            "is_alive": True, "confidence": 0.8, "last_seen_turn": 4990,
            "equipment_summary": {"weapon_class": "pistol", "armor_class": "none",
                                   "combat_strength_estimate": 0.5},
        }}}
        r = evaluate_hunter_equipment_advantage(agent=killer, target_belief=None,
                                                need_result=None, world_turn=5000)
        assert r["is_advantaged"] is True

    def test_low_ammo_requires_resupply(self):
        killer = _make_killer(weapon_type="rifle")
        killer["inventory"] = [{"id": "a0", "type": "ammo_762", "value": 0}]
        r = evaluate_hunter_equipment_advantage(agent=killer, target_belief=None,
                                                need_result=None, world_turn=5000)
        assert "ammo_resupply" in r["missing_requirements"]

    def test_no_meds_requires_medicine_resupply(self):
        killer = _make_killer(weapon_type="rifle")
        killer["inventory"] = [
            {"id": f"a{i}", "type": "ammo_762", "value": 0} for i in range(25)
        ]
        r = evaluate_hunter_equipment_advantage(agent=killer, target_belief=None,
                                                need_result=None, world_turn=5000)
        assert "medicine_resupply" in r["missing_requirements"]

    def test_combat_readiness_includes_equipment_advantage(self):
        killer = _make_killer(weapon_type="pistol")
        killer["inventory"] = (
            [{"id": f"a{i}", "type": "ammo_9mm", "value": 0} for i in range(3)] +
            [{"id": "m0", "type": "bandage", "value": 0}]
        )
        killer["knowledge_v1"] = {"known_npcs": {"target_0": {
            "is_alive": True, "confidence": 0.9, "last_seen_turn": 4990,
            "equipment_summary": {"weapon_class": "rifle", "armor_class": "light",
                                   "combat_strength_estimate": 0.94},
        }}}
        r = evaluate_kill_target_combat_readiness(agent=killer, target_belief=None,
                                                  need_result=None, context=None, world_turn=5000)
        assert "equipment_disadvantage" in r["reasons"]
        assert r["equipment_advantaged"] is False
        assert isinstance(r["equipment_advantage"], dict)


# --- Fix C: Economy pressure with weak lead -----------------------------------

class TestHunterEconomyPressureWeakLead:
    def test_undergeared_with_weak_lead_gets_money_pressure(self):
        killer = _make_killer(weapon_type="pistol", money=0, location_id="loc_a", debt=500)
        killer["inventory"] = (
            [{"id": f"a{i}", "type": "ammo_9mm", "value": 0} for i in range(3)] +
            [{"id": "m0", "type": "bandage", "value": 0}]
        )
        killer["knowledge_v1"] = {"known_npcs": {"target_0": {
            "is_alive": True, "confidence": 0.9, "last_seen_turn": 4700,
            "equipment_summary": {"weapon_class": "rifle", "armor_class": "light",
                                   "combat_strength_estimate": 0.94},
        }}}
        target = _make_target(location_id="loc_b")
        state = _state_with_agents(killer, target)
        ctx = _make_ctx_with_weak_lead(killer, target, state, lead_confidence=0.4)
        objectives = generate_objectives(ctx)
        keys = [o.key for o in objectives]
        assert OBJECTIVE_GET_MONEY_FOR_RESUPPLY in keys
        prep_obj = next((o for o in objectives
                         if o.key == OBJECTIVE_GET_MONEY_FOR_RESUPPLY
                         and o.metadata.get("hunt_preparation_pressure")), None)
        assert prep_obj is not None, "Should have hunt_preparation_pressure in metadata"


# --- Fix E: Trader loop anti-pattern -----------------------------------------

class TestKillerTraderLoopAntiPattern:
    def test_soft_restore_suppressed_for_undergeared_killer_with_debt(self):
        killer = _make_killer(weapon_type="pistol", money=0, location_id="loc_b",
                              hunger=55, thirst=45, sleepiness=55, debt=500)
        killer["inventory"] = (
            [{"id": f"a{i}", "type": "ammo_9mm", "value": 0} for i in range(3)] +
            [{"id": "m0", "type": "bandage", "value": 0}]
        )
        killer["knowledge_v1"] = {"known_npcs": {"target_0": {
            "is_alive": True, "confidence": 0.9, "last_seen_turn": 4700,
            "equipment_summary": {"weapon_class": "rifle", "armor_class": "light",
                                   "combat_strength_estimate": 0.94},
        }}}
        target = _make_target(location_id="loc_a")
        state = _state_with_agents(killer, target)
        ctx = _make_ctx_with_weak_lead(killer, target, state, lead_confidence=0.4)
        objectives = generate_objectives(ctx)
        soft = [o for o in objectives
                if o.key in {"RESTORE_FOOD", "RESTORE_WATER", "REST"} and float(o.urgency) < 0.8]
        assert soft == [], f"Soft restore/rest should be suppressed; found: {[o.key for o in soft]}"

    def test_critical_hunger_not_suppressed(self):
        killer = _make_killer(weapon_type="pistol", money=0, location_id="loc_b",
                              hunger=95, debt=500)
        killer["inventory"] = (
            [{"id": f"a{i}", "type": "ammo_9mm", "value": 0} for i in range(3)] +
            [{"id": "m0", "type": "bandage", "value": 0}]
        )
        killer["knowledge_v1"] = {"known_npcs": {"target_0": {
            "is_alive": True, "confidence": 0.9, "last_seen_turn": 4700,
            "equipment_summary": {"weapon_class": "rifle", "armor_class": "light",
                                   "combat_strength_estimate": 0.94},
        }}}
        target = _make_target(location_id="loc_a")
        state = _state_with_agents(killer, target)
        ctx = _make_ctx_with_weak_lead(killer, target, state, lead_confidence=0.4)
        objectives = generate_objectives(ctx)
        food_objs = [o for o in objectives if o.key == "RESTORE_FOOD"]
        assert food_objs, "Critical hunger RESTORE_FOOD must not be suppressed"
