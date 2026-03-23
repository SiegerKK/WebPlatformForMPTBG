"""Tests for plan builder (planner.py)."""
from __future__ import annotations

import pytest

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.models.intent import (
    Intent,
    INTENT_GET_RICH,
    INTENT_HEAL_SELF,
    INTENT_LEAVE_ZONE,
    INTENT_SEEK_FOOD,
    INTENT_SEEK_WATER,
    INTENT_SELL_ARTIFACTS,
)
from app.games.zone_stalkers.decision.models.plan import (
    STEP_TRAVEL_TO_LOCATION,
    STEP_TRADE_SELL_ITEM,
    STEP_EXPLORE_LOCATION,
    STEP_CONSUME_ITEM,
    STEP_WAIT,
)
from app.games.zone_stalkers.decision.planner import build_plan
from tests.decision.conftest import make_agent, make_minimal_state, make_state_with_trader


def _make_intent(kind, score=0.5):
    return Intent(kind=kind, score=score, created_turn=100)


def _plan_for(agent_id="bot1", agent=None, state=None, intent_kind=INTENT_GET_RICH):
    if agent is None:
        agent = make_agent()
    if state is None:
        state = make_minimal_state(agent_id=agent_id, agent=agent)
    ctx = build_agent_context(agent_id, agent, state)
    intent = _make_intent(intent_kind)
    return build_plan(ctx, intent, state, 100)


class TestGetRichPlan:
    def test_has_artifacts_trader_at_same_loc_sells(self):
        """get_rich + artifacts + trader at same location → STEP_TRADE_SELL_ITEM."""
        from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
        artifact_type = next(iter(ARTIFACT_TYPES))
        agent = make_agent(inventory=[{"type": artifact_type, "value": 500}])
        state = make_state_with_trader(agent=agent, trader_at="loc_a")
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_GET_RICH)
        assert plan.steps[0].kind == STEP_TRADE_SELL_ITEM

    def test_has_artifacts_trader_elsewhere_travels(self):
        """get_rich + artifacts + trader at other location → STEP_TRAVEL_TO_LOCATION."""
        from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
        artifact_type = next(iter(ARTIFACT_TYPES))
        agent = make_agent(inventory=[{"type": artifact_type, "value": 500}])
        state = make_state_with_trader(agent=agent, trader_at="loc_b")
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_GET_RICH)
        assert plan.steps[0].kind == STEP_TRAVEL_TO_LOCATION
        assert plan.steps[0].payload["target_id"] == "loc_b"

    def test_no_artifacts_anomaly_at_current_loc_explores(self):
        """get_rich + no artifacts + anomaly at current loc → STEP_EXPLORE_LOCATION."""
        agent = make_agent(has_ammo=True)
        state = make_minimal_state(agent=agent)
        # Add anomaly activity to loc_a (current location)
        state["locations"]["loc_a"]["anomaly_activity"] = 5
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_GET_RICH)
        assert plan.steps[0].kind == STEP_EXPLORE_LOCATION

    def test_no_artifacts_no_anomaly_travels_to_anomaly(self):
        """get_rich + no artifacts + no anomaly at loc_a but anomaly at loc_b → STEP_TRAVEL."""
        agent = make_agent()
        state = make_minimal_state(agent=agent)
        # loc_a has no anomaly (default 0), loc_b has anomaly_activity=5
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_GET_RICH)
        assert plan.steps[0].kind == STEP_TRAVEL_TO_LOCATION
        assert plan.steps[0].payload["target_id"] == "loc_b"

    def test_no_artifacts_no_anomaly_anywhere_waits(self):
        """get_rich + no artifacts + no anomaly anywhere → STEP_WAIT."""
        agent = make_agent()
        state = make_minimal_state(agent=agent)
        state["locations"]["loc_b"]["anomaly_activity"] = 0
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_GET_RICH)
        assert plan.steps[0].kind == STEP_WAIT


class TestHealSelfPlan:
    def test_has_heal_item_consumes(self):
        """heal_self + heal item in inventory → STEP_CONSUME_ITEM."""
        from app.games.zone_stalkers.balance.items import HEAL_ITEM_TYPES
        heal_type = next(iter(HEAL_ITEM_TYPES))
        agent = make_agent(hp=20, inventory=[{"type": heal_type, "value": 100}])
        state = make_minimal_state(agent=agent)
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_HEAL_SELF)
        assert plan.steps[0].kind == STEP_CONSUME_ITEM

    def test_no_heal_item_travels_to_trader(self):
        """heal_self + no heal item + trader exists → STEP_TRAVEL_TO_LOCATION."""
        agent = make_agent(hp=20)
        state = make_state_with_trader(agent=agent, trader_at="loc_b")
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_HEAL_SELF)
        assert plan.steps[0].kind == STEP_TRAVEL_TO_LOCATION
        assert plan.steps[0].payload["target_id"] == "loc_b"


class TestLeaveZonePlan:
    def test_leave_zone_travels_to_exit(self):
        """leave_zone intent → STEP_TRAVEL_TO_LOCATION to exit_zone location."""
        agent = make_agent(global_goal_achieved=True)
        state = make_minimal_state(agent=agent)
        state["locations"]["loc_b"]["exit_zone"] = True
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_LEAVE_ZONE)
        assert plan.steps[0].kind == STEP_TRAVEL_TO_LOCATION
        assert plan.steps[0].payload["target_id"] == "loc_b"

    def test_leave_zone_no_exit_returns_wait_fallback(self):
        """leave_zone intent but no exit_zone in map → idle fallback plan."""
        agent = make_agent(global_goal_achieved=True)
        state = make_minimal_state(agent=agent)
        # No exit_zone on any location → _plan_leave_zone returns None → idle plan
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_LEAVE_ZONE)
        assert plan.steps[0].kind == STEP_WAIT


class TestSellArtifactsPlan:
    def test_sell_at_same_location(self):
        from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
        artifact_type = next(iter(ARTIFACT_TYPES))
        agent = make_agent(inventory=[{"type": artifact_type, "value": 500}])
        state = make_state_with_trader(agent=agent, trader_at="loc_a")
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_SELL_ARTIFACTS)
        assert plan.steps[0].kind == STEP_TRADE_SELL_ITEM

    def test_travel_to_sell(self):
        from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
        artifact_type = next(iter(ARTIFACT_TYPES))
        agent = make_agent(inventory=[{"type": artifact_type, "value": 500}])
        state = make_state_with_trader(agent=agent, trader_at="loc_b")
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_SELL_ARTIFACTS)
        assert plan.steps[0].kind == STEP_TRAVEL_TO_LOCATION


class TestSeekConsumablePlan:
    """Tests for seek_water / seek_food plan builder, including opportunistic consumption."""

    # ── seek_water ─────────────────────────────────────────────────────────────

    def test_seek_water_has_water_consumes_immediately(self):
        """seek_water + water in inventory → single STEP_CONSUME_ITEM (no travel)."""
        agent = make_agent(thirst=80, inventory=[{"type": "water", "value": 10}])
        state = make_state_with_trader(agent=agent, trader_at="loc_b")
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_SEEK_WATER)
        assert len(plan.steps) == 1
        assert plan.steps[0].kind == STEP_CONSUME_ITEM
        assert plan.steps[0].payload["item_type"] == "water"

    def test_seek_water_no_water_no_food_just_travels(self):
        """seek_water + no water + no food → travel + buy (no prepend)."""
        agent = make_agent(thirst=80, hunger=50)
        state = make_state_with_trader(agent=agent, trader_at="loc_b")
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_SEEK_WATER)
        assert plan.steps[0].kind == STEP_TRAVEL_TO_LOCATION
        assert plan.steps[0].payload["target_id"] == "loc_b"

    def test_seek_water_no_water_has_food_hungry_eats_first(self):
        """Bug fix: seek_water + no water + food in inventory + hunger ≥ 25 → eat food first."""
        agent = make_agent(thirst=80, hunger=50, inventory=[{"type": "bread", "value": 10}])
        state = make_state_with_trader(agent=agent, trader_at="loc_b")
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_SEEK_WATER)
        # First step: eat the food; remaining: travel + buy water
        assert plan.steps[0].kind == STEP_CONSUME_ITEM
        assert plan.steps[0].payload["item_type"] == "bread"
        assert plan.steps[0].payload["reason"] == "opportunistic_food"
        assert plan.steps[1].kind == STEP_TRAVEL_TO_LOCATION
        assert len(plan.steps) == 3

    def test_seek_water_no_water_has_food_not_hungry_just_travels(self):
        """seek_water + no water + food in inventory + hunger < 25 → no opportunistic eat."""
        agent = make_agent(thirst=80, hunger=10, inventory=[{"type": "bread", "value": 10}])
        state = make_state_with_trader(agent=agent, trader_at="loc_b")
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_SEEK_WATER)
        assert plan.steps[0].kind == STEP_TRAVEL_TO_LOCATION
        assert len(plan.steps) == 2

    # ── seek_food ──────────────────────────────────────────────────────────────

    def test_seek_food_has_food_consumes_immediately(self):
        """seek_food + food in inventory → single STEP_CONSUME_ITEM (no travel)."""
        agent = make_agent(hunger=80, inventory=[{"type": "bread", "value": 10}])
        state = make_state_with_trader(agent=agent, trader_at="loc_b")
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_SEEK_FOOD)
        assert len(plan.steps) == 1
        assert plan.steps[0].kind == STEP_CONSUME_ITEM
        assert plan.steps[0].payload["item_type"] == "bread"

    def test_seek_food_no_food_has_water_thirsty_drinks_first(self):
        """seek_food + no food + water in inventory + thirst ≥ 25 → drink water first."""
        agent = make_agent(hunger=80, thirst=60, inventory=[{"type": "water", "value": 10}])
        state = make_state_with_trader(agent=agent, trader_at="loc_b")
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_SEEK_FOOD)
        assert plan.steps[0].kind == STEP_CONSUME_ITEM
        assert plan.steps[0].payload["item_type"] == "water"
        assert plan.steps[0].payload["reason"] == "opportunistic_drink"
        assert plan.steps[1].kind == STEP_TRAVEL_TO_LOCATION
        assert len(plan.steps) == 3

    def test_seek_food_no_food_has_water_not_thirsty_just_travels(self):
        """seek_food + no food + water in inventory + thirst < 25 → no opportunistic drink."""
        agent = make_agent(hunger=80, thirst=5, inventory=[{"type": "water", "value": 10}])
        state = make_state_with_trader(agent=agent, trader_at="loc_b")
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_SEEK_FOOD)
        assert plan.steps[0].kind == STEP_TRAVEL_TO_LOCATION
        assert len(plan.steps) == 2


# ── Bug 1: sell artifact before buying consumable when money == 0 ─────────────

class TestSeekConsumableSellFirst:
    """NPC with zero money + artifact → plan sells artifact before buying."""

    def _artifact_agent(self, **kwargs):
        from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
        artifact_type = next(iter(ARTIFACT_TYPES))
        inv = [{"id": "art1", "type": artifact_type, "value": 500}]
        return make_agent(money=0, inventory=inv, **kwargs)

    def test_seek_water_no_money_artifact_at_trader_sells_first(self):
        """seek_water + money=0 + artifact + at trader → step 0 = SELL, step 1 = BUY."""
        agent = self._artifact_agent(thirst=80)
        state = make_state_with_trader(agent=agent, trader_at="loc_a")
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_SEEK_WATER)
        assert plan.steps[0].kind == STEP_TRADE_SELL_ITEM, (
            f"Expected SELL first, got {plan.steps[0].kind}"
        )
        from app.games.zone_stalkers.decision.models.plan import STEP_TRADE_BUY_ITEM
        assert plan.steps[1].kind == STEP_TRADE_BUY_ITEM, (
            f"Expected BUY second, got {plan.steps[1].kind}"
        )
        assert plan.steps[1].payload.get("item_category") == "drink"

    def test_seek_food_no_money_artifact_at_trader_sells_first(self):
        """seek_food + money=0 + artifact + at trader → step 0 = SELL, step 1 = BUY food."""
        agent = self._artifact_agent(hunger=80)
        state = make_state_with_trader(agent=agent, trader_at="loc_a")
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_SEEK_FOOD)
        assert plan.steps[0].kind == STEP_TRADE_SELL_ITEM
        from app.games.zone_stalkers.decision.models.plan import STEP_TRADE_BUY_ITEM
        assert plan.steps[1].kind == STEP_TRADE_BUY_ITEM
        assert plan.steps[1].payload.get("item_category") == "food"

    def test_seek_water_has_money_no_sell_step(self):
        """Regression: money > 0 + artifact + at trader → just BUY (no sell step)."""
        from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
        artifact_type = next(iter(ARTIFACT_TYPES))
        agent = make_agent(
            money=500, thirst=80,
            inventory=[{"id": "art1", "type": artifact_type, "value": 500}],
        )
        state = make_state_with_trader(agent=agent, trader_at="loc_a")
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_SEEK_WATER)
        from app.games.zone_stalkers.decision.models.plan import STEP_TRADE_BUY_ITEM
        assert plan.steps[0].kind == STEP_TRADE_BUY_ITEM, (
            f"Should go straight to BUY when money is available, got {plan.steps[0].kind}"
        )
        assert len(plan.steps) == 1

    def test_seek_water_no_money_no_artifact_no_sell_step(self):
        """money=0 but no artifact → no sell step (just travel+buy as before)."""
        agent = make_agent(money=0, thirst=80)
        state = make_state_with_trader(agent=agent, trader_at="loc_b")
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_SEEK_WATER)
        # No artifact to sell → falls through to travel+buy
        assert plan.steps[0].kind == STEP_TRAVEL_TO_LOCATION

    def test_seek_water_no_money_artifact_trader_elsewhere_travels(self):
        """money=0 + artifact + trader at loc_b → travel first (sell on arrival, handled by
        fresh plan rebuild), so first step is still TRAVEL."""
        agent = self._artifact_agent(thirst=80)
        state = make_state_with_trader(agent=agent, trader_at="loc_b")
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_SEEK_WATER)
        # Agent must travel first; sell-before-buy fires when agent arrives (plan rebuilds)
        assert plan.steps[0].kind == STEP_TRAVEL_TO_LOCATION
        assert plan.steps[0].payload["target_id"] == "loc_b"

    def test_heal_self_no_money_artifact_at_trader_sells_first(self):
        """heal_self + money=0 + artifact + at trader → SELL before BUY medical."""
        from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
        artifact_type = next(iter(ARTIFACT_TYPES))
        agent = make_agent(
            hp=20, money=0,
            inventory=[{"id": "art1", "type": artifact_type, "value": 500}],
        )
        state = make_state_with_trader(agent=agent, trader_at="loc_a")
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_HEAL_SELF)
        assert plan.steps[0].kind == STEP_TRADE_SELL_ITEM
        from app.games.zone_stalkers.decision.models.plan import STEP_TRADE_BUY_ITEM
        assert plan.steps[1].kind == STEP_TRADE_BUY_ITEM
        assert plan.steps[1].payload.get("item_category") == "medical"


# ── Bug 2: equipped-weapon check before generating buy-weapon plan ─────────────

class TestResupplyWeaponGuard:
    """NPC with weapon equipped must not be assigned a buy-weapon plan."""

    def test_no_weapon_generates_buy_weapon_plan(self):
        """INTENT_RESUPPLY + no weapon + wealthy + at trader → buys weapon."""
        from app.games.zone_stalkers.decision.models.plan import STEP_TRADE_BUY_ITEM
        from app.games.zone_stalkers.decision.models.intent import INTENT_RESUPPLY
        agent = make_agent(has_weapon=False, has_armor=True, has_ammo=False,
                           money=5000, material_threshold=3000)
        state = make_state_with_trader(agent=agent, trader_at="loc_a")
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_RESUPPLY)
        assert plan.steps[0].kind == STEP_TRADE_BUY_ITEM
        assert plan.steps[0].payload.get("item_category") == "weapon", (
            f"Should buy weapon, got category={plan.steps[0].payload.get('item_category')}"
        )

    def test_weapon_equipped_no_armor_buys_armor_not_weapon(self):
        """INTENT_RESUPPLY + weapon equipped + no armor + wealthy + at trader → buys armor."""
        from app.games.zone_stalkers.decision.models.plan import STEP_TRADE_BUY_ITEM
        from app.games.zone_stalkers.decision.models.intent import INTENT_RESUPPLY
        agent = make_agent(has_weapon=True, has_armor=False, has_ammo=True,
                           money=5000, material_threshold=3000)
        state = make_state_with_trader(agent=agent, trader_at="loc_a")
        plan = _plan_for(agent=agent, state=state, intent_kind=INTENT_RESUPPLY)
        assert plan.steps[0].kind == STEP_TRADE_BUY_ITEM
        assert plan.steps[0].payload.get("item_category") == "armor", (
            f"Should buy armor (not weapon), got category={plan.steps[0].payload.get('item_category')}"
        )

    def test_weapon_equipped_reload_or_rearm_low(self):
        """reload_or_rearm need is 0.0 when weapon+armor+ammo all equipped."""
        from app.games.zone_stalkers.decision.needs import evaluate_needs
        from app.games.zone_stalkers.decision.context_builder import build_agent_context
        agent = make_agent(has_weapon=True, has_armor=True, has_ammo=True)
        state = make_minimal_state(agent=agent)
        ctx = build_agent_context("bot1", agent, state)
        needs = evaluate_needs(ctx, state)
        assert needs.reload_or_rearm == 0.0, (
            f"reload_or_rearm should be 0.0 when fully equipped, got {needs.reload_or_rearm}"
        )

    def test_no_weapon_reload_or_rearm_is_065(self):
        """reload_or_rearm == 0.65 when no weapon equipped."""
        from app.games.zone_stalkers.decision.needs import evaluate_needs
        from app.games.zone_stalkers.decision.context_builder import build_agent_context
        agent = make_agent(has_weapon=False, has_armor=False, has_ammo=False)
        state = make_minimal_state(agent=agent)
        ctx = build_agent_context("bot1", agent, state)
        needs = evaluate_needs(ctx, state)
        assert needs.reload_or_rearm == 0.65

    def test_executor_guard_skips_weapon_buy_when_equipped(self):
        """_exec_trade_buy with category='weapon' returns [] when weapon already equipped."""
        from app.games.zone_stalkers.decision.executors import execute_plan_step
        from app.games.zone_stalkers.decision.models.plan import Plan, PlanStep, STEP_TRADE_BUY_ITEM
        from app.games.zone_stalkers.decision.models.intent import INTENT_RESUPPLY
        from app.games.zone_stalkers.decision.context_builder import build_agent_context

        agent = make_agent(has_weapon=True, has_armor=True, has_ammo=True,
                           money=5000, material_threshold=3000)
        state = make_state_with_trader(agent=agent, trader_at="loc_a")
        ctx = build_agent_context("bot1", agent, state)

        # Manually craft a buy-weapon plan step (simulates stale plan or miscategorised step)
        plan = Plan(
            intent_kind=INTENT_RESUPPLY,
            steps=[PlanStep(STEP_TRADE_BUY_ITEM, {"item_category": "weapon"},
                            interruptible=False)],
            confidence=0.8, created_turn=100,
        )
        events = execute_plan_step(ctx, plan, state, 100)
        # Should be empty: guard prevented the duplicate weapon purchase
        assert events == [], (
            f"Guard should block weapon purchase when already equipped; got events={events}"
        )
        # Money must be unchanged
        assert state["agents"]["bot1"]["money"] == 5000

    def test_executor_guard_skips_equipment_buy_when_weapon_equipped(self):
        """_exec_trade_buy with legacy category='equipment' also blocked when weapon equipped."""
        from app.games.zone_stalkers.decision.executors import execute_plan_step
        from app.games.zone_stalkers.decision.models.plan import Plan, PlanStep, STEP_TRADE_BUY_ITEM
        from app.games.zone_stalkers.decision.models.intent import INTENT_RESUPPLY
        from app.games.zone_stalkers.decision.context_builder import build_agent_context

        agent = make_agent(has_weapon=True, has_armor=True, has_ammo=True,
                           money=5000, material_threshold=3000)
        state = make_state_with_trader(agent=agent, trader_at="loc_a")
        ctx = build_agent_context("bot1", agent, state)

        plan = Plan(
            intent_kind=INTENT_RESUPPLY,
            steps=[PlanStep(STEP_TRADE_BUY_ITEM, {"item_category": "equipment"},
                            interruptible=False)],
            confidence=0.8, created_turn=100,
        )
        events = execute_plan_step(ctx, plan, state, 100)
        assert events == [], (
            "Guard should block 'equipment' category buy when weapon already equipped"
        )
        assert state["agents"]["bot1"]["money"] == 5000
