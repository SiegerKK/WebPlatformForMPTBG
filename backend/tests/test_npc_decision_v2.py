"""Tests for NPC Decision Architecture v2 modules.

Tests cover Phases 1–4:
  - Models instantiation
  - context_builder
  - needs evaluator (NeedScores formulas)
  - intent selector (priority / tie-break)
  - plan builder
  - bridges (plan_from_scheduled_action)
  - explain_intent debug output
"""
import pytest
from dataclasses import asdict


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_agent(
    agent_id="agent_1",
    hp=100,
    hunger=0,
    thirst=0,
    sleepiness=0,
    money=500,
    global_goal="get_rich",
    material_threshold=3000,
    location_id="loc_a",
    has_weapon=False,
    has_armor=False,
    has_ammo=False,
    kill_target_id=None,
    global_goal_achieved=False,
):
    agent = {
        "archetype": "stalker_agent",
        "controller": {"kind": "bot"},
        "is_alive": True,
        "has_left_zone": False,
        "location_id": location_id,
        "hp": hp,
        "hunger": hunger,
        "thirst": thirst,
        "sleepiness": sleepiness,
        "money": money,
        "global_goal": global_goal,
        "material_threshold": material_threshold,
        "equipment": {},
        "inventory": [],
        "memory": [],
        "name": agent_id,
        "skill_stalker": 1,
        "risk_tolerance": 0.5,
    }
    if has_weapon:
        agent["equipment"]["weapon"] = {"type": "makarov_pistol", "value": 300}
    if has_armor:
        agent["equipment"]["armor"] = {"type": "leather_jacket", "value": 200}
    if has_ammo and has_weapon:
        agent["inventory"].append({"type": "pistol_ammo", "quantity": 20, "value": 50})
    if kill_target_id:
        agent["kill_target_id"] = kill_target_id
        agent["global_goal"] = "kill_stalker"
    if global_goal_achieved:
        agent["global_goal_achieved"] = True
    return agent


def _make_minimal_state(agent_id="agent_1", agent=None, loc_terrain="buildings"):
    if agent is None:
        agent = _make_agent(agent_id=agent_id)
    return {
        "world_turn": 100,
        "world_day": 1,
        "world_hour": 12,
        "world_minute": 0,
        "emission_active": False,
        "agents": {agent_id: agent},
        "locations": {
            "loc_a": {
                "name": "Локация А",
                "terrain_type": loc_terrain,
                "anomaly_activity": 0,
                "connections": [{"to": "loc_b", "travel_time": 12}],
                "items": [],
            },
            "loc_b": {
                "name": "Локация Б",
                "terrain_type": "buildings",
                "anomaly_activity": 5,
                "connections": [{"to": "loc_a", "travel_time": 12}],
                "items": [],
            },
        },
        "combat_interactions": {},
        "relations": {},
        "groups": {},
    }


# ── Phase 1: Models ───────────────────────────────────────────────────────────

class TestModels:
    def test_agent_context_instantiation(self):
        from app.games.zone_stalkers.decision.models.agent_context import AgentContext
        ctx = AgentContext(
            agent_id="a1",
            self_state={"hp": 100},
            location_state={"name": "TestLoc"},
            world_context={"world_turn": 1},
        )
        assert ctx.agent_id == "a1"
        assert ctx.visible_entities == []
        assert ctx.combat_context is None

    def test_need_scores_defaults(self):
        from app.games.zone_stalkers.decision.models.need_scores import NeedScores
        ns = NeedScores()
        assert ns.survive_now == 0.0
        assert ns.heal_self == 0.0
        for val in asdict(ns).values():
            assert val == 0.0

    def test_intent_instantiation(self):
        from app.games.zone_stalkers.decision.models.intent import Intent, INTENT_HEAL_SELF
        intent = Intent(kind=INTENT_HEAL_SELF, score=0.8, reason="Low HP")
        assert intent.kind == INTENT_HEAL_SELF
        assert intent.score == 0.8
        assert intent.source_goal is None

    def test_plan_step_instantiation(self):
        from app.games.zone_stalkers.decision.models.plan import PlanStep, STEP_TRAVEL_TO_LOCATION
        step = PlanStep(kind=STEP_TRAVEL_TO_LOCATION, payload={"target_id": "loc_b"})
        assert step.kind == STEP_TRAVEL_TO_LOCATION
        assert step.interruptible is True

    def test_plan_current_step_and_advance(self):
        from app.games.zone_stalkers.decision.models.plan import Plan, PlanStep, STEP_WAIT
        step1 = PlanStep(kind=STEP_WAIT)
        step2 = PlanStep(kind=STEP_WAIT)
        plan = Plan(intent_kind="idle", steps=[step1, step2])
        assert plan.current_step is step1
        assert not plan.is_complete
        plan.advance()
        assert plan.current_step is step2
        plan.advance()
        assert plan.is_complete
        assert plan.current_step is None

    def test_relation_state_defaults(self):
        from app.games.zone_stalkers.decision.models.relation_state import RelationState, ATTITUDE_NEUTRAL
        rel = RelationState()
        assert rel.attitude == ATTITUDE_NEUTRAL
        assert rel.trust == 0.0
        assert rel.hostility == 0.0

    def test_group_state_instantiation(self):
        from app.games.zone_stalkers.decision.models.group_state import GroupState, GROUP_STATUS_ACTIVE
        gs = GroupState(group_id="g1", leader_id="a1", members=["a1", "a2"])
        assert gs.status == GROUP_STATUS_ACTIVE
        assert len(gs.members) == 2


# ── Phase 1: Context Builder ──────────────────────────────────────────────────

class TestContextBuilder:
    def test_basic_context(self):
        from app.games.zone_stalkers.decision.context_builder import build_agent_context
        agent = _make_agent()
        state = _make_minimal_state(agent=agent)
        ctx = build_agent_context("agent_1", agent, state)
        assert ctx.agent_id == "agent_1"
        assert ctx.self_state is agent
        assert ctx.location_state["name"] == "Локация А"
        assert ctx.world_context["world_turn"] == 100

    def test_visible_entities_colocated(self):
        from app.games.zone_stalkers.decision.context_builder import build_agent_context
        agent1 = _make_agent(agent_id="a1", location_id="loc_a")
        agent2 = _make_agent(agent_id="a2", location_id="loc_a")
        agent3 = _make_agent(agent_id="a3", location_id="loc_b")
        state = _make_minimal_state(agent_id="a1", agent=agent1)
        state["agents"]["a2"] = agent2
        state["agents"]["a3"] = agent3
        ctx = build_agent_context("a1", agent1, state)
        visible_ids = [e["agent_id"] for e in ctx.visible_entities]
        assert "a2" in visible_ids
        assert "a3" not in visible_ids

    def test_no_visible_dead_agents(self):
        from app.games.zone_stalkers.decision.context_builder import build_agent_context
        agent1 = _make_agent(agent_id="a1", location_id="loc_a")
        agent2 = _make_agent(agent_id="a2", location_id="loc_a")
        agent2["is_alive"] = False
        state = _make_minimal_state(agent_id="a1", agent=agent1)
        state["agents"]["a2"] = agent2
        ctx = build_agent_context("a1", agent1, state)
        assert len(ctx.visible_entities) == 0

    def test_combat_context_present(self):
        from app.games.zone_stalkers.decision.context_builder import build_agent_context
        agent = _make_agent()
        state = _make_minimal_state(agent=agent)
        state["combat_interactions"]["agent_1"] = {"enemies": ["enemy_1"]}
        ctx = build_agent_context("agent_1", agent, state)
        assert ctx.combat_context is not None
        assert ctx.combat_context["enemies"] == ["enemy_1"]

    def test_combat_context_absent(self):
        from app.games.zone_stalkers.decision.context_builder import build_agent_context
        agent = _make_agent()
        state = _make_minimal_state(agent=agent)
        ctx = build_agent_context("agent_1", agent, state)
        assert ctx.combat_context is None

    def test_known_targets_populated(self):
        from app.games.zone_stalkers.decision.context_builder import build_agent_context
        hunter = _make_agent(agent_id="hunter", kill_target_id="prey")
        prey = _make_agent(agent_id="prey", location_id="loc_b")
        state = _make_minimal_state(agent_id="hunter", agent=hunter)
        state["agents"]["prey"] = prey
        ctx = build_agent_context("hunter", hunter, state)
        assert len(ctx.known_targets) == 1
        assert ctx.known_targets[0]["agent_id"] == "prey"


# ── Phase 2: NeedScores ───────────────────────────────────────────────────────

class TestNeedScores:
    def _eval(self, **kwargs):
        from app.games.zone_stalkers.decision.context_builder import build_agent_context
        from app.games.zone_stalkers.decision.needs import evaluate_needs
        agent = _make_agent(**kwargs)
        state = _make_minimal_state(agent=agent)
        ctx = build_agent_context("agent_1", agent, state)
        return evaluate_needs(ctx, state)

    def test_survive_now_critical(self):
        needs = self._eval(hp=5)
        assert needs.survive_now == 1.0

    def test_survive_now_zero_at_full_hp(self):
        needs = self._eval(hp=100)
        assert needs.survive_now == 0.0

    def test_survive_now_threshold(self):
        needs = self._eval(hp=30)
        assert needs.survive_now == 0.0

    def test_heal_self_critical(self):
        needs = self._eval(hp=15)
        assert needs.heal_self == 1.0

    def test_heal_self_moderate(self):
        needs = self._eval(hp=40)
        assert 0.0 < needs.heal_self < 1.0

    def test_eat_zero_when_not_hungry(self):
        needs = self._eval(hunger=0)
        assert needs.eat == 0.0

    def test_eat_high_when_hungry(self):
        needs = self._eval(hunger=80)
        assert needs.eat == 0.8

    def test_drink_high_when_thirsty(self):
        needs = self._eval(thirst=70)
        assert abs(needs.drink - 0.7) < 0.01

    def test_sleep_zero_when_rested(self):
        needs = self._eval(sleepiness=0)
        assert needs.sleep == 0.0

    def test_reload_or_rearm_no_weapon(self):
        needs = self._eval(has_weapon=False)
        assert needs.reload_or_rearm == 1.0

    def test_reload_or_rearm_with_weapon_no_armor(self):
        needs = self._eval(has_weapon=True, has_armor=False)
        assert needs.reload_or_rearm == 0.7

    def test_reload_or_rearm_equipped(self):
        needs = self._eval(has_weapon=True, has_armor=True, has_ammo=True)
        assert needs.reload_or_rearm == 0.0

    def test_get_rich_below_threshold(self):
        from app.games.zone_stalkers.decision.needs import GET_RICH_WEIGHT
        needs = self._eval(money=0, material_threshold=3000)
        # With wealth=0: get_rich = (1.0 - 0) * GET_RICH_WEIGHT = GET_RICH_WEIGHT
        assert abs(needs.get_rich - GET_RICH_WEIGHT) < 0.01

    def test_get_rich_at_threshold(self):
        needs = self._eval(money=3000, material_threshold=3000)
        assert needs.get_rich == 0.0

    def test_avoid_emission_no_emission(self):
        needs = self._eval()
        assert needs.avoid_emission == 0.0

    def test_avoid_emission_active_dangerous_terrain(self):
        from app.games.zone_stalkers.decision.context_builder import build_agent_context
        from app.games.zone_stalkers.decision.needs import evaluate_needs
        agent = _make_agent(location_id="loc_a")
        state = _make_minimal_state(agent=agent, loc_terrain="plain")
        state["emission_active"] = True
        ctx = build_agent_context("agent_1", agent, state)
        needs = evaluate_needs(ctx, state)
        assert needs.avoid_emission == 1.0

    def test_leave_zone_when_goal_achieved(self):
        needs = self._eval(global_goal_achieved=True)
        assert needs.leave_zone == 1.0

    def test_leave_zone_zero_otherwise(self):
        needs = self._eval(global_goal_achieved=False)
        assert needs.leave_zone == 0.0

    def test_all_scores_in_range(self):
        needs = self._eval(hp=25, hunger=60, thirst=50, sleepiness=40, money=500)
        for name, val in asdict(needs).items():
            assert 0.0 <= val <= 1.0, f"{name}={val} out of [0,1]"


# ── Phase 3: Intent Selection ─────────────────────────────────────────────────

class TestIntentSelection:
    def _select(self, **kwargs):
        from app.games.zone_stalkers.decision.context_builder import build_agent_context
        from app.games.zone_stalkers.decision.needs import evaluate_needs
        from app.games.zone_stalkers.decision.intents import select_intent
        agent = _make_agent(**kwargs)
        state = _make_minimal_state(agent=agent)
        ctx = build_agent_context("agent_1", agent, state)
        needs = evaluate_needs(ctx, state)
        return select_intent(ctx, needs, world_turn=100)

    def test_intent_escape_danger_at_critical_hp(self):
        from app.games.zone_stalkers.decision.models.intent import INTENT_ESCAPE_DANGER
        intent = self._select(hp=5)
        assert intent.kind == INTENT_ESCAPE_DANGER

    def test_intent_heal_self_at_low_hp(self):
        from app.games.zone_stalkers.decision.models.intent import INTENT_HEAL_SELF
        # Use hp=35 and fully equipped so heal_self is the dominant drive
        intent = self._select(hp=35, hunger=0, thirst=0, sleepiness=0,
                               has_weapon=True, has_armor=True, has_ammo=True)
        assert intent.kind == INTENT_HEAL_SELF

    def test_intent_seek_water_beats_eat_when_thirstier(self):
        from app.games.zone_stalkers.decision.models.intent import INTENT_SEEK_WATER
        intent = self._select(hp=100, thirst=90, hunger=50, has_weapon=True, has_armor=True)
        assert intent.kind == INTENT_SEEK_WATER

    def test_intent_seek_food_when_hungry(self):
        from app.games.zone_stalkers.decision.models.intent import INTENT_SEEK_FOOD
        intent = self._select(hp=100, hunger=90, thirst=10, has_weapon=True, has_armor=True)
        assert intent.kind == INTENT_SEEK_FOOD

    def test_intent_resupply_when_no_weapon(self):
        from app.games.zone_stalkers.decision.models.intent import INTENT_RESUPPLY
        intent = self._select(hp=100, hunger=0, thirst=0, has_weapon=False)
        assert intent.kind == INTENT_RESUPPLY

    def test_intent_get_rich_when_poor(self):
        from app.games.zone_stalkers.decision.models.intent import INTENT_GET_RICH
        intent = self._select(hp=100, hunger=0, thirst=0, has_weapon=True, has_armor=True,
                              money=0, material_threshold=3000)
        assert intent.kind == INTENT_GET_RICH

    def test_intent_leave_zone_when_goal_achieved(self):
        from app.games.zone_stalkers.decision.models.intent import INTENT_LEAVE_ZONE
        intent = self._select(global_goal_achieved=True, has_weapon=True, has_armor=True,
                              money=5000, material_threshold=3000)
        assert intent.kind == INTENT_LEAVE_ZONE

    def test_intent_has_score(self):
        intent = self._select(hp=5)
        assert intent.score > 0.0

    def test_intent_has_reason(self):
        intent = self._select(hp=5)
        assert intent.reason is not None
        assert len(intent.reason) > 0

    def test_intent_wait_in_shelter_on_safe_terrain_with_emission(self):
        from app.games.zone_stalkers.decision.context_builder import build_agent_context
        from app.games.zone_stalkers.decision.needs import evaluate_needs
        from app.games.zone_stalkers.decision.intents import select_intent
        from app.games.zone_stalkers.decision.models.intent import INTENT_WAIT_IN_SHELTER
        agent = _make_agent(hp=100, hunger=0, thirst=0, has_weapon=True, has_armor=True,
                             money=5000, material_threshold=3000)
        state = _make_minimal_state(agent=agent, loc_terrain="buildings")
        state["emission_active"] = True
        ctx = build_agent_context("agent_1", agent, state)
        needs = evaluate_needs(ctx, state)
        intent = select_intent(ctx, needs, world_turn=100)
        assert intent.kind == INTENT_WAIT_IN_SHELTER

    def test_intent_flee_emission_on_dangerous_terrain(self):
        from app.games.zone_stalkers.decision.context_builder import build_agent_context
        from app.games.zone_stalkers.decision.needs import evaluate_needs
        from app.games.zone_stalkers.decision.intents import select_intent
        from app.games.zone_stalkers.decision.models.intent import INTENT_FLEE_EMISSION
        agent = _make_agent(hp=100, hunger=0, thirst=0, has_weapon=True, has_armor=True,
                             money=5000, material_threshold=3000)
        state = _make_minimal_state(agent=agent, loc_terrain="plain")
        state["emission_active"] = True
        ctx = build_agent_context("agent_1", agent, state)
        needs = evaluate_needs(ctx, state)
        intent = select_intent(ctx, needs, world_turn=100)
        assert intent.kind == INTENT_FLEE_EMISSION

    def test_intent_hunt_target_when_above_threshold(self):
        from app.games.zone_stalkers.decision.models.intent import INTENT_HUNT_TARGET
        intent = self._select(hp=100, hunger=0, thirst=0, has_weapon=True, has_armor=True,
                               money=5000, material_threshold=3000, kill_target_id="enemy_1")
        assert intent.kind == INTENT_HUNT_TARGET


# ── Phase 1: Bridges ─────────────────────────────────────────────────────────

class TestBridges:
    def test_plan_from_travel_scheduled_action(self):
        from app.games.zone_stalkers.decision.bridges import plan_from_scheduled_action
        from app.games.zone_stalkers.decision.models.plan import STEP_TRAVEL_TO_LOCATION
        agent = _make_agent()
        agent["scheduled_action"] = {
            "type": "travel",
            "target_id": "loc_b",
            "turns_remaining": 12,
            "turns_total": 12,
        }
        plan = plan_from_scheduled_action(agent, world_turn=100)
        assert plan is not None
        assert len(plan.steps) == 1
        assert plan.steps[0].kind == STEP_TRAVEL_TO_LOCATION
        assert plan.steps[0].payload["target_id"] == "loc_b"

    def test_plan_from_sleep_scheduled_action(self):
        from app.games.zone_stalkers.decision.bridges import plan_from_scheduled_action
        from app.games.zone_stalkers.decision.models.plan import STEP_SLEEP_FOR_HOURS
        from app.games.zone_stalkers.decision.models.intent import INTENT_REST
        agent = _make_agent()
        agent["scheduled_action"] = {
            "type": "sleep",
            "hours": 6,
            "turns_remaining": 360,
            "turns_total": 360,
        }
        plan = plan_from_scheduled_action(agent, world_turn=100)
        assert plan is not None
        assert plan.steps[0].kind == STEP_SLEEP_FOR_HOURS
        assert plan.intent_kind == INTENT_REST

    def test_plan_from_none_returns_none(self):
        from app.games.zone_stalkers.decision.bridges import plan_from_scheduled_action
        agent = _make_agent()
        assert agent.get("scheduled_action") is None
        result = plan_from_scheduled_action(agent)
        assert result is None

    def test_scheduled_action_from_travel_step(self):
        from app.games.zone_stalkers.decision.bridges import scheduled_action_from_plan_step
        from app.games.zone_stalkers.decision.models.plan import PlanStep, STEP_TRAVEL_TO_LOCATION
        step = PlanStep(
            kind=STEP_TRAVEL_TO_LOCATION,
            payload={"target_id": "loc_b", "turns_remaining": 12, "turns_total": 12},
        )
        sched = scheduled_action_from_plan_step(step)
        assert sched["type"] == "travel"
        assert sched["target_id"] == "loc_b"

    def test_scheduled_action_from_sleep_step(self):
        from app.games.zone_stalkers.decision.bridges import scheduled_action_from_plan_step
        from app.games.zone_stalkers.decision.models.plan import PlanStep, STEP_SLEEP_FOR_HOURS
        step = PlanStep(
            kind=STEP_SLEEP_FOR_HOURS,
            payload={"hours": 8, "turns_remaining": 480, "turns_total": 480},
        )
        sched = scheduled_action_from_plan_step(step)
        assert sched["type"] == "sleep"
        assert sched["hours"] == 8


# ── Phase 4: Planner ─────────────────────────────────────────────────────────

class TestPlanner:
    def _plan(self, intent_kind, **agent_kwargs):
        from app.games.zone_stalkers.decision.context_builder import build_agent_context
        from app.games.zone_stalkers.decision.models.intent import Intent
        from app.games.zone_stalkers.decision.planner import build_plan
        agent = _make_agent(**agent_kwargs)
        state = _make_minimal_state(agent=agent)
        ctx = build_agent_context("agent_1", agent, state)
        intent = Intent(kind=intent_kind, score=0.8, created_turn=100)
        return build_plan(ctx, intent, state, world_turn=100)

    def test_plan_flee_emission_has_travel_step(self):
        from app.games.zone_stalkers.decision.models.intent import INTENT_FLEE_EMISSION
        from app.games.zone_stalkers.decision.models.plan import STEP_TRAVEL_TO_LOCATION
        from app.games.zone_stalkers.decision.context_builder import build_agent_context
        from app.games.zone_stalkers.decision.models.intent import Intent
        from app.games.zone_stalkers.decision.planner import build_plan
        agent = _make_agent(location_id="loc_a")
        state = _make_minimal_state(agent=agent, loc_terrain="plain")
        state["locations"]["loc_b"]["terrain_type"] = "buildings"
        state["emission_active"] = True
        ctx = build_agent_context("agent_1", agent, state)
        intent = Intent(kind=INTENT_FLEE_EMISSION, score=1.0, created_turn=100)
        plan = build_plan(ctx, intent, state, world_turn=100)
        assert plan is not None
        assert plan.steps[0].kind == STEP_TRAVEL_TO_LOCATION

    def test_plan_wait_in_shelter_has_wait_step(self):
        from app.games.zone_stalkers.decision.models.intent import INTENT_WAIT_IN_SHELTER
        from app.games.zone_stalkers.decision.models.plan import STEP_WAIT
        plan = self._plan(INTENT_WAIT_IN_SHELTER)
        assert plan.steps[0].kind == STEP_WAIT
        assert not plan.interruptible

    def test_plan_rest_has_sleep_step(self):
        from app.games.zone_stalkers.decision.models.intent import INTENT_REST
        from app.games.zone_stalkers.decision.models.plan import STEP_SLEEP_FOR_HOURS
        plan = self._plan(INTENT_REST)
        assert plan.steps[0].kind == STEP_SLEEP_FOR_HOURS

    def test_plan_idle_is_not_none(self):
        from app.games.zone_stalkers.decision.models.intent import INTENT_IDLE
        from app.games.zone_stalkers.decision.models.plan import STEP_WAIT
        plan = self._plan(INTENT_IDLE)
        assert plan is not None
        assert plan.steps[0].kind == STEP_WAIT

    def test_plan_created_turn_set(self):
        from app.games.zone_stalkers.decision.models.intent import INTENT_IDLE
        plan = self._plan(INTENT_IDLE)
        assert plan.created_turn == 100

    def test_plan_confidence_in_range(self):
        from app.games.zone_stalkers.decision.models.intent import INTENT_IDLE
        plan = self._plan(INTENT_IDLE)
        assert 0.0 <= plan.confidence <= 1.0


# ── Social: Relations ─────────────────────────────────────────────────────────

class TestRelations:
    def test_get_neutral_default(self):
        from app.games.zone_stalkers.decision.social.relations import get_relation
        from app.games.zone_stalkers.decision.models.relation_state import ATTITUDE_NEUTRAL
        state = {"relations": {}}
        rel = get_relation("a1", "a2", state)
        assert rel.attitude == ATTITUDE_NEUTRAL
        assert rel.trust == 0.0

    def test_set_and_get_relation(self):
        from app.games.zone_stalkers.decision.social.relations import get_relation, set_relation
        from app.games.zone_stalkers.decision.models.relation_state import RelationState
        state = {}
        rel = RelationState(trust=0.5, hostility=0.1)
        set_relation("a1", "a2", rel, state)
        retrieved = get_relation("a1", "a2", state)
        assert abs(retrieved.trust - 0.5) < 0.001

    def test_update_relation_combat_attacked(self):
        from app.games.zone_stalkers.decision.social.relations import (
            update_relation_from_event, get_relation,
        )
        state = {}
        update_relation_from_event("a1", "a2", "combat_attacked", 100, state)
        rel = get_relation("a1", "a2", state)
        assert rel.hostility > 0.0
        assert rel.trust < 0.0

    def test_update_relation_trade_completed(self):
        from app.games.zone_stalkers.decision.social.relations import (
            update_relation_from_event, get_relation,
        )
        state = {}
        update_relation_from_event("a1", "a2", "trade_completed", 100, state)
        rel = get_relation("a1", "a2", state)
        assert rel.trust > 0.0
        assert rel.last_interaction_type == "trade_completed"
        assert rel.last_interaction_turn == 100


# ── Debug: explain_intent ─────────────────────────────────────────────────────

class TestExplainIntent:
    def test_explain_returns_dict(self):
        from app.games.zone_stalkers.decision.debug.explain_intent import explain_agent_decision
        agent = _make_agent()
        state = _make_minimal_state(agent=agent)
        result = explain_agent_decision("agent_1", state)
        assert isinstance(result, dict)
        assert result["agent_id"] == "agent_1"

    def test_explain_has_all_required_keys(self):
        from app.games.zone_stalkers.decision.debug.explain_intent import explain_agent_decision
        agent = _make_agent()
        state = _make_minimal_state(agent=agent)
        result = explain_agent_decision("agent_1", state)
        for key in ("context_summary", "need_scores", "selected_intent", "active_plan"):
            assert key in result, f"Missing key: {key}"

    def test_explain_invalid_agent(self):
        from app.games.zone_stalkers.decision.debug.explain_intent import explain_agent_decision
        state = {"agents": {}}
        result = explain_agent_decision("nonexistent", state)
        assert "error" in result

    def test_explain_need_scores_has_top3(self):
        from app.games.zone_stalkers.decision.debug.explain_intent import explain_agent_decision
        agent = _make_agent(hp=20)
        state = _make_minimal_state(agent=agent)
        result = explain_agent_decision("agent_1", state)
        assert "top_3" in result["need_scores"]
        assert len(result["need_scores"]["top_3"]) > 0

    def test_explain_selected_intent_has_kind(self):
        from app.games.zone_stalkers.decision.debug.explain_intent import explain_agent_decision
        agent = _make_agent()
        state = _make_minimal_state(agent=agent)
        result = explain_agent_decision("agent_1", state)
        assert "kind" in result["selected_intent"]
        assert result["selected_intent"]["kind"] != ""

    def test_summarise_all_bots_list(self):
        from app.games.zone_stalkers.decision.debug.explain_intent import summarise_all_bots
        agent1 = _make_agent(agent_id="b1")
        agent2 = _make_agent(agent_id="b2")
        state = _make_minimal_state(agent_id="b1", agent=agent1)
        state["agents"]["b2"] = agent2
        results = summarise_all_bots(state)
        assert len(results) == 2
        ids = [r["agent_id"] for r in results]
        assert "b1" in ids
        assert "b2" in ids

    def test_summarise_excludes_dead_agents(self):
        from app.games.zone_stalkers.decision.debug.explain_intent import summarise_all_bots
        agent1 = _make_agent(agent_id="alive")
        agent2 = _make_agent(agent_id="dead")
        agent2["is_alive"] = False
        state = _make_minimal_state(agent_id="alive", agent=agent1)
        state["agents"]["dead"] = agent2
        results = summarise_all_bots(state)
        assert len(results) == 1
        assert results[0]["agent_id"] == "alive"
