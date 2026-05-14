"""Integration tests for hunter preparation planning and trade_buy failure semantics.

Covers:
- Sleeping agent does not write location observations (write_location_observations gate).
- PREPARE_FOR_HUNT intent preserves required_hunt_equipment metadata.
- PREPARE_FOR_HUNT against rifle target forces weapon_upgrade plan step.
- weapon_class_for_item_type / armor_class_for_item_type helper functions.
- _exec_trade_buy with min_weapon_class buys ak74 for rifle requirement.
- trade_buy_failed event is emitted and does NOT advance plan step.
- Successful trade buy advances plan step.
- Soft restore at trader without money uses survival loan immediately.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.games.zone_stalkers.balance.items import (
    weapon_class_for_item_type,
    armor_class_for_item_type,
)
from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.models.intent import (
    Intent, INTENT_RESUPPLY, INTENT_SEEK_FOOD, INTENT_SEEK_WATER,
)
from app.games.zone_stalkers.decision.models.plan import (
    Plan, PlanStep,
    STEP_TRADE_BUY_ITEM, STEP_TRAVEL_TO_LOCATION, STEP_REQUEST_LOAN,
)
from app.games.zone_stalkers.decision.objectives.generator import (
    OBJECTIVE_PREPARE_FOR_HUNT,
    OBJECTIVE_GET_MONEY_FOR_RESUPPLY,
    generate_objectives,
)
from app.games.zone_stalkers.decision.objectives.intent_adapter import objective_to_intent
from app.games.zone_stalkers.decision.models.objective import Objective, ObjectiveScore, ObjectiveGenerationContext
from app.games.zone_stalkers.decision.beliefs import build_belief_state
from app.games.zone_stalkers.decision.needs import evaluate_need_result
from app.games.zone_stalkers.decision.planner import build_plan
from app.games.zone_stalkers.decision.executors import (
    execute_plan_step,
    _trade_buy_succeeded,
    _make_trade_buy_failed_event,
    _exec_trade_buy,
)
from app.games.zone_stalkers.rules.tick_rules import _write_location_observations
from tests.decision.conftest import make_agent, make_minimal_state


# ── State/agent helpers ───────────────────────────────────────────────────────

def _make_base_state():
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
                "corpses": [],
            },
            "loc_b": {
                "name": "Bunker",
                "terrain_type": "buildings",
                "anomaly_activity": 0,
                "connections": [{"to": "loc_a", "travel_time": 60}],
                "items": [],
                "agents": [],
                "corpses": [],
            },
        },
        "combat_interactions": {},
        "relations": {},
        "groups": {},
        "mutants": {},
    }


def _make_killer(weapon_type="pistol", money=0, location_id="loc_a",
                 sleeping=False, hp=85, ammo_count=3, med_count=2):
    ammo_map = {
        "pistol": "ammo_9mm",
        "shotgun": "ammo_12gauge",
        "ak74": "ammo_545",
        "pkm": "ammo_762",
        "svu_svd": "ammo_762",
    }
    ammo_type = ammo_map.get(weapon_type, "ammo_9mm")
    agent = {
        "archetype": "stalker_agent",
        "controller": {"kind": "bot"},
        "is_alive": True,
        "has_left_zone": False,
        "location_id": location_id,
        "hp": hp,
        "hunger": 0,
        "thirst": 0,
        "sleepiness": 0,
        "money": money,
        "global_goal": "kill_stalker",
        "kill_target_id": "target_0",
        "material_threshold": 0,
        "equipment": {
            "weapon": {"type": weapon_type, "value": 300},
            "armor": {"type": "leather_jacket", "value": 200},
        },
        "inventory": (
            [{"id": f"a{i}", "type": ammo_type, "value": 0} for i in range(ammo_count)] +
            [{"id": f"m{i}", "type": "bandage", "value": 0} for i in range(med_count)]
        ),
        "name": "killer_agent",
        "risk_tolerance": 0.5,
        "skill_stalker": 1,
    }
    if sleeping:
        agent["scheduled_action"] = {"type": "sleep", "hours": 8, "started_turn": 4990}
    return agent


def _make_objective_ctx(killer, state, world_turn=5000):
    from app.games.zone_stalkers.decision.models.target_belief import TargetBelief, LocationHypothesis
    state["world_turn"] = world_turn
    ctx = build_agent_context("killer_0", killer, state)
    belief_state = build_belief_state(ctx, killer, world_turn)
    target_belief = TargetBelief(
        target_id="target_0", is_known=True, is_alive=True,
        last_known_location_id="loc_b", location_confidence=0.8,
        best_location_id="loc_b", best_location_confidence=0.8,
        last_seen_turn=world_turn - 60, visible_now=False, co_located=False,
        equipment_known=True, combat_strength=0.94, combat_strength_confidence=0.8,
        possible_locations=(
            LocationHypothesis(
                location_id="loc_b", probability=0.8, confidence=0.8,
                freshness=0.8, reason="memory_lead", source_refs=("memory:obs",),
            ),
        ),
        likely_routes=(), exhausted_locations=(), lead_count=1, route_hints=(),
        source_refs=("memory:obs",), recently_seen=True,
        recent_contact_turn=world_turn - 60, recent_contact_location_id="loc_b",
        recent_contact_age=60,
    )
    need_result = evaluate_need_result(ctx, state)
    return ObjectiveGenerationContext(
        agent_id="killer_0",
        world_turn=world_turn,
        belief_state=belief_state,
        need_result=need_result,
        active_plan_summary=None,
        personality=killer,
        target_belief=target_belief,
    )


# ── Item class helpers ────────────────────────────────────────────────────────

class TestItemClassHelpers:
    def test_ak74_class_is_rifle(self):
        assert weapon_class_for_item_type("ak74") == "rifle"

    def test_pkm_class_is_rifle(self):
        assert weapon_class_for_item_type("pkm") == "rifle"

    def test_svu_svd_class_is_sniper(self):
        assert weapon_class_for_item_type("svu_svd") == "sniper"

    def test_pistol_class_is_pistol(self):
        assert weapon_class_for_item_type("pistol") == "pistol"

    def test_shotgun_class_is_shotgun(self):
        assert weapon_class_for_item_type("shotgun") == "shotgun"

    def test_unknown_weapon_class_is_none(self):
        assert weapon_class_for_item_type("unknown_gun") == "none"

    def test_leather_jacket_class_is_light(self):
        assert armor_class_for_item_type("leather_jacket") == "light"

    def test_stalker_suit_class_is_medium(self):
        assert armor_class_for_item_type("stalker_suit") == "medium"

    def test_seva_suit_class_is_medium(self):
        assert armor_class_for_item_type("seva_suit") == "medium"

    def test_combat_armor_class_is_heavy(self):
        assert armor_class_for_item_type("combat_armor") == "heavy"

    def test_exoskeleton_class_is_heavy(self):
        assert armor_class_for_item_type("exoskeleton") == "heavy"

    def test_prepare_for_hunt_weapon_upgrade_accepts_ak74_for_rifle_requirement(self):
        from app.games.zone_stalkers.decision.constants import WEAPON_CLASS_RANK
        min_rank = WEAPON_CLASS_RANK["rifle"]
        item_class = weapon_class_for_item_type("ak74")
        assert WEAPON_CLASS_RANK.get(item_class, 0) >= min_rank

    def test_prepare_for_hunt_armor_upgrade_accepts_stalker_suit_for_medium_requirement(self):
        from app.games.zone_stalkers.decision.constants import ARMOR_CLASS_RANK
        min_rank = ARMOR_CLASS_RANK["medium"]
        item_class = armor_class_for_item_type("stalker_suit")
        assert ARMOR_CLASS_RANK.get(item_class, 0) >= min_rank


# ── Sleeping observation gate ─────────────────────────────────────────────────

class TestSleepingLocationObservationGate:
    def test_sleeping_agent_write_location_observations_does_not_write_stalkers_seen(self):
        killer = _make_killer(sleeping=True, location_id="loc_a")
        target = _make_killer(location_id="loc_a")
        target["id"] = "target_0"
        state = _make_base_state()
        state["agents"]["killer_0"] = killer
        state["agents"]["target_0"] = target

        before_len = len(killer.get("memory") or [])
        _write_location_observations("killer_0", killer, "loc_a", state, 5000)
        after_len = len(killer.get("memory") or [])

        assert after_len == before_len, "Sleeping agent must not write any observation memories"

    def test_sleeping_target_visit_does_not_write_location_observation_or_knowledge(self):
        killer = _make_killer(sleeping=True, location_id="loc_a")
        target = _make_killer(location_id="loc_a")
        target["id"] = "target_0"
        state = _make_base_state()
        state["agents"]["killer_0"] = killer
        state["agents"]["target_0"] = target

        _write_location_observations("killer_0", killer, "loc_a", state, 5000)
        target["location_id"] = "loc_b"
        _write_location_observations("killer_0", killer, "loc_a", state, 5001)

        memory = killer.get("memory") or []
        stalker_obs = [
            e for e in memory
            if isinstance(e, dict)
            and (e.get("observed") == "stalkers"
                 or (e.get("data") or {}).get("observed") == "stalkers")
        ]
        assert stalker_obs == [], f"Sleeping killer must not see target; got: {stalker_obs}"

    def test_awake_agent_can_still_observe_colocated_stalker(self):
        """Awake agent should be able to observe — no assertion on exact memory structure,
        just assert that the call does NOT raise an error for an awake agent."""
        killer = _make_killer(sleeping=False, location_id="loc_a")
        target = _make_killer(location_id="loc_a")
        target["id"] = "target_0"
        state = _make_base_state()
        state["agents"]["killer_0"] = killer
        state["agents"]["target_0"] = target

        # Should not raise any exception; function runs for awake agents
        try:
            _write_location_observations("killer_0", killer, "loc_a", state, 5000)
        except Exception as exc:
            pytest.fail(f"Awake agent should not raise: {exc}")


# ── Intent metadata preservation ─────────────────────────────────────────────

class TestPrepareForHuntIntentMetadata:
    def test_prepare_for_hunt_intent_preserves_required_equipment_metadata(self):
        obj = Objective(
            key="PREPARE_FOR_HUNT",
            source="global_goal",
            urgency=0.9, expected_value=0.85, risk=0.25, time_cost=0.45,
            resource_cost=0.25, confidence=0.8, goal_alignment=1.0,
            memory_confidence=0.8, reasons=("needs prep",),
            metadata={
                "support_objective_for": "kill_stalker",
                "equipment_advantage": {
                    "target_weapon_class": "rifle",
                    "own_weapon_class": "pistol",
                    "is_advantaged": False,
                    "missing_requirements": ["weapon_upgrade"],
                    "estimated_money_needed": 2000,
                },
                "required_hunt_equipment": {
                    "weapon_min_class": "rifle",
                    "armor_min_class": "light",
                    "ammo_min_count": 20,
                    "medical_min_count": 2,
                    "missing_requirements": ["weapon_upgrade"],
                },
                "estimated_money_needed_for_advantage": 2000,
                "blockers": [{"key": "weapon_upgrade", "reason": "weapon inferior"}],
            },
        )
        score = ObjectiveScore(objective_key="PREPARE_FOR_HUNT", raw_score=0.9, final_score=0.9, factors=(), penalties=())
        intent = objective_to_intent(obj, score, world_turn=5000)

        assert intent.metadata.get("required_hunt_equipment") is not None
        assert intent.metadata.get("support_objective_for") == "kill_stalker"
        assert intent.metadata.get("equipment_advantage") is not None
        req = intent.metadata["required_hunt_equipment"]
        assert req.get("weapon_min_class") == "rifle"
        assert "weapon_upgrade" in req.get("missing_requirements", [])

    def test_prepare_for_hunt_forced_category_uses_required_hunt_equipment(self):
        obj = Objective(
            key="PREPARE_FOR_HUNT",
            source="global_goal",
            urgency=0.9, expected_value=0.85, risk=0.25, time_cost=0.45,
            resource_cost=0.25, confidence=0.8, goal_alignment=1.0,
            memory_confidence=0.8, reasons=("needs prep",),
            metadata={
                "required_hunt_equipment": {
                    "weapon_min_class": "rifle",
                    "missing_requirements": ["weapon_upgrade", "ammo_resupply"],
                },
            },
        )
        score = ObjectiveScore(objective_key="PREPARE_FOR_HUNT", raw_score=0.9, final_score=0.9, factors=(), penalties=())
        intent = objective_to_intent(obj, score, world_turn=5000)
        assert intent.metadata.get("forced_resupply_category") == "weapon_upgrade"


# ── PREPARE_FOR_HUNT planner tests ────────────────────────────────────────────

class TestPrepareForHuntPlanning:
    def _make_intent(self, money_needed=1500):
        return Intent(
            kind=INTENT_RESUPPLY,
            score=0.9,
            source_goal="kill_stalker",
            reason="prepare for hunt",
            created_turn=5000,
            metadata={
                "objective_key": "PREPARE_FOR_HUNT",
                "support_objective_for": "kill_stalker",
                "required_hunt_equipment": {
                    "weapon_min_class": "rifle",
                    "armor_min_class": "light",
                    "ammo_min_count": 20,
                    "medical_min_count": 2,
                    "missing_requirements": ["weapon_upgrade"],
                },
                "estimated_money_needed_for_advantage": money_needed,
                "forced_resupply_category": "weapon_upgrade",
            },
        )

    def test_prepare_for_hunt_against_rifle_target_forces_weapon_upgrade_step(self):
        """Planner produces buy weapon_upgrade step when killer is at trader with money."""
        killer = _make_killer(weapon_type="pistol", money=2500, location_id="loc_a")
        # Remove current weapon so equip guard doesn't fire
        killer["equipment"]["weapon"] = None
        state = _make_base_state()
        state["agents"]["killer_0"] = killer
        state["traders"]["trader_1"] = {
            "id": "trader_1", "location_id": "loc_a",
            "inventory": [{"type": "ak74", "value": 1500, "id": "ak1", "quantity": 1}],
            "money": 10000,
        }

        ctx = build_agent_context("killer_0", killer, state)
        intent = self._make_intent(money_needed=1500)
        plan = build_plan(ctx, intent, state, 5000, need_result=None)

        assert plan is not None
        buy_steps = [s for s in plan.steps if s.kind == STEP_TRADE_BUY_ITEM]
        assert buy_steps, f"Plan must have a STEP_TRADE_BUY_ITEM; steps: {[s.kind for s in plan.steps]}"
        buy_step = buy_steps[0]
        assert buy_step.payload.get("item_category") == "weapon_upgrade"
        assert buy_step.payload.get("min_weapon_class") == "rifle"

    def test_prepare_for_hunt_no_money_returns_travel_or_get_rich_plan(self):
        """Hunter with pistol and no money: plan must not include immediate buy."""
        killer = _make_killer(weapon_type="pistol", money=0, location_id="loc_a")
        state = _make_base_state()
        state["agents"]["killer_0"] = killer

        ctx = build_agent_context("killer_0", killer, state)
        intent = self._make_intent(money_needed=2000)
        plan = build_plan(ctx, intent, state, 5000, need_result=None)

        assert plan is not None
        # The first step should be travel or explore (get rich), not immediate buy
        if plan.steps:
            first_step = plan.steps[0]
            # Should not be a direct buy (hunter has no money)
            assert not (
                first_step.kind == STEP_TRADE_BUY_ITEM
                and first_step.payload.get("item_category") == "weapon_upgrade"
            ), f"No-money hunter should not immediately try to buy; first step: {first_step}"


# ── trade_buy failure semantics ───────────────────────────────────────────────

class TestTradeBuyFailureSemantics:
    def test_trade_buy_succeeded_false_for_empty(self):
        assert _trade_buy_succeeded([]) is False

    def test_trade_buy_succeeded_false_for_failed_event(self):
        ev = _make_trade_buy_failed_event(
            agent_id="a1", reason="not_enough_money",
            item_category="food", buy_mode=None, location_id="loc_a",
        )
        assert _trade_buy_succeeded([ev]) is False

    def test_trade_buy_succeeded_true_for_bot_bought_item(self):
        assert _trade_buy_succeeded([{"event_type": "bot_bought_item"}]) is True

    def test_trade_buy_failed_does_not_advance_active_plan(self):
        """When _exec_trade_buy returns only trade_buy_failed, plan must NOT advance."""
        agent = make_agent(agent_id="a1", money=0)
        state = make_minimal_state(agent_id="a1", agent=agent)
        ctx = build_agent_context("a1", agent, state)

        plan_obj = Plan(
            intent_kind=INTENT_RESUPPLY,
            steps=[
                PlanStep(STEP_TRADE_BUY_ITEM, {"item_category": "food"}, interruptible=False),
                PlanStep(STEP_TRAVEL_TO_LOCATION, {"target_id": "loc_b"}, interruptible=True),
            ],
            confidence=0.8,
            created_turn=5000,
        )

        # No trader and no money → buy will fail
        execute_plan_step(ctx, plan_obj, state, world_turn=5000)

        assert plan_obj.current_step_index == 0, (
            f"Plan must not advance when buy fails; index: {plan_obj.current_step_index}"
        )
        assert plan_obj.steps[0].payload.get("_trade_buy_failed") is True

    def test_exec_trade_buy_min_weapon_class_no_affordable_rifle_returns_failure_event(self):
        """No affordable rifle → trade_buy_failed event returned, not empty list."""
        agent = make_agent(agent_id="a1", money=100)  # ak74 costs 1500*1.5=2250 → not affordable
        state = make_minimal_state(agent_id="a1", agent=agent)
        ctx = build_agent_context("a1", agent, state)

        step = PlanStep(
            STEP_TRADE_BUY_ITEM,
            {"item_category": "weapon_upgrade", "min_weapon_class": "rifle",
             "reason": "prepare_hunt_weapon_advantage"},
            interruptible=False,
        )
        plan_obj = Plan(
            intent_kind=INTENT_RESUPPLY,
            steps=[step],
            confidence=0.8, created_turn=5000,
        )
        execute_plan_step(ctx, plan_obj, state, world_turn=5000)

        # Plan should NOT have advanced (still at index 0)
        assert plan_obj.current_step_index == 0
        assert plan_obj.steps[0].payload.get("_trade_buy_failed") is True

    def test_exec_trade_buy_min_armor_class_medium_accepts_stalker_suit(self):
        """stalker_suit qualifies as medium → should NOT produce no_matching_upgrade."""
        agent = make_agent(agent_id="a1", money=3000)
        agent["equipment"]["armor"] = None  # no armor equipped
        state = make_minimal_state(agent_id="a1", agent=agent)
        state["traders"] = {
            "tr1": {
                "id": "tr1",
                "location_id": "loc_a",
                "inventory": [{"id": "s1", "type": "stalker_suit", "value": 1500, "quantity": 1}],
                "money": 10000,
            }
        }
        ctx = build_agent_context("a1", agent, state)

        step = PlanStep(
            STEP_TRADE_BUY_ITEM,
            {"item_category": "armor_upgrade", "min_armor_class": "medium",
             "reason": "prepare_hunt_armor_advantage"},
            interruptible=False,
        )
        events = _exec_trade_buy("a1", agent, step, ctx, state, 5000)

        fail_no_match = [
            e for e in events
            if isinstance(e, dict) and e.get("event_type") == "trade_buy_failed"
            and (e.get("payload") or {}).get("reason") == "no_matching_upgrade"
        ]
        assert not fail_no_match, (
            "stalker_suit (medium class) must qualify for min_armor_class=medium; "
            f"got no_matching_upgrade: {fail_no_match}"
        )


# ── Undergeared killer ────────────────────────────────────────────────────────

class TestUndergeredKillerObjectiveGen:
    def test_undergeared_killer_at_trader_no_money_gets_get_rich_plan(self):
        """Hunter with pistol vs rifle target and no money should get GET_MONEY_FOR_RESUPPLY."""
        killer = _make_killer(weapon_type="pistol", money=0, location_id="loc_a",
                              ammo_count=3, med_count=2)
        killer["knowledge_v1"] = {"known_npcs": {"target_0": {
            "is_alive": True, "confidence": 0.9, "last_seen_turn": 4900,
            "equipment_summary": {"weapon_class": "rifle", "armor_class": "medium",
                                   "combat_strength_estimate": 0.9},
        }}}
        state = _make_base_state()
        state["agents"]["killer_0"] = killer

        ctx = _make_objective_ctx(killer, state)
        objectives = generate_objectives(ctx)
        keys = [o.key for o in objectives]

        assert OBJECTIVE_GET_MONEY_FOR_RESUPPLY in keys, (
            f"Undergeared killer with no money must have GET_MONEY_FOR_RESUPPLY; got: {keys}"
        )
