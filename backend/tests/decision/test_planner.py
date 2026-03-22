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
