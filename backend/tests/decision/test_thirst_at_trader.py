"""Tests for the thirst-at-trader bug fix.

Regression: NPCs with high thirst died when standing next to a trader even
when they had enough money to buy water.  Root cause: ``_plan_seek_consumable``
only generated a plan when ``trader_loc != agent_loc`` — if the agent was
*already* at the trader location the condition was False and the function
returned ``None``, resulting in an idle/wait plan instead of a buy action.

Same structural bug existed in ``_plan_heal_or_flee`` and is also covered here.
"""
from __future__ import annotations

import pytest

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.needs import evaluate_needs
from app.games.zone_stalkers.decision.intents import select_intent
from app.games.zone_stalkers.decision.planner import build_plan
from app.games.zone_stalkers.decision.executors import execute_plan_step
from app.games.zone_stalkers.decision.models.plan import (
    STEP_TRADE_BUY_ITEM,
    STEP_TRAVEL_TO_LOCATION,
    STEP_CONSUME_ITEM,
)
from app.games.zone_stalkers.decision.models.intent import (
    Intent,
    INTENT_SEEK_WATER,
    INTENT_SEEK_FOOD,
    INTENT_HEAL_SELF,
)
from tests.decision.conftest import make_agent, make_state_with_trader


def _make_intent(kind: str, score: float = 0.95) -> Intent:
    return Intent(kind=kind, score=score, created_turn=100)


def _build_plan(agent, state, intent_kind, agent_id="bot1"):
    ctx = build_agent_context(agent_id, agent, state)
    intent = _make_intent(intent_kind)
    return build_plan(ctx, intent, state, 100)


# ── Planner: seek_water ────────────────────────────────────────────────────────

class TestSeekWaterAtTrader:
    """NPC at trader location with no water in inventory → plan buy immediately."""

    def test_high_thirst_at_trader_plans_buy_not_travel(self):
        """Regression: thirst≥75 + no water + trader at same location → STEP_TRADE_BUY_ITEM.

        Before the fix this returned None (→ idle), causing the NPC to die of
        thirst while standing next to a trader.
        """
        agent = make_agent(thirst=80, money=500, location_id="loc_a", inventory=[])
        state = make_state_with_trader(agent=agent, trader_at="loc_a")
        plan = _build_plan(agent, state, INTENT_SEEK_WATER)
        assert plan is not None, "Expected a plan but got None"
        assert plan.steps[0].kind == STEP_TRADE_BUY_ITEM, (
            f"Expected STEP_TRADE_BUY_ITEM but got {plan.steps[0].kind!r}; "
            "NPC should buy water immediately, not travel"
        )
        assert plan.steps[0].payload.get("item_category") == "drink"

    def test_high_thirst_at_trader_single_step_plan(self):
        """Co-located buy plan must have exactly one step (no travel step)."""
        agent = make_agent(thirst=80, money=500, location_id="loc_a", inventory=[])
        state = make_state_with_trader(agent=agent, trader_at="loc_a")
        plan = _build_plan(agent, state, INTENT_SEEK_WATER)
        assert len(plan.steps) == 1, (
            f"Buy-at-trader plan should have 1 step; got {len(plan.steps)}"
        )

    def test_high_thirst_trader_elsewhere_still_travels(self):
        """Sanity: trader at *different* location → plan still starts with travel."""
        agent = make_agent(thirst=80, money=500, location_id="loc_a", inventory=[])
        state = make_state_with_trader(agent=agent, trader_at="loc_b")
        plan = _build_plan(agent, state, INTENT_SEEK_WATER)
        assert plan.steps[0].kind == STEP_TRAVEL_TO_LOCATION

    def test_has_water_at_trader_consumes_inventory_first(self):
        """If water is already in inventory, consume it — don't buy."""
        agent = make_agent(thirst=80, money=500, location_id="loc_a",
                           inventory=[{"type": "water", "value": 10}])
        state = make_state_with_trader(agent=agent, trader_at="loc_a")
        plan = _build_plan(agent, state, INTENT_SEEK_WATER)
        assert plan.steps[0].kind == STEP_CONSUME_ITEM
        assert plan.steps[0].payload["item_type"] == "water"


# ── Planner: seek_food ─────────────────────────────────────────────────────────

class TestSeekFoodAtTrader:
    """Same fix applies to food (seek_food intent)."""

    def test_high_hunger_at_trader_plans_buy_not_travel(self):
        """hunger≥75 + no food + trader at same location → STEP_TRADE_BUY_ITEM."""
        agent = make_agent(hunger=80, money=500, location_id="loc_a", inventory=[])
        state = make_state_with_trader(agent=agent, trader_at="loc_a")
        plan = _build_plan(agent, state, INTENT_SEEK_FOOD)
        assert plan is not None
        assert plan.steps[0].kind == STEP_TRADE_BUY_ITEM
        assert plan.steps[0].payload.get("item_category") == "food"

    def test_high_hunger_at_trader_single_step_plan(self):
        agent = make_agent(hunger=80, money=500, location_id="loc_a", inventory=[])
        state = make_state_with_trader(agent=agent, trader_at="loc_a")
        plan = _build_plan(agent, state, INTENT_SEEK_FOOD)
        assert len(plan.steps) == 1


# ── Planner: heal_self ─────────────────────────────────────────────────────────

class TestHealSelfAtTrader:
    """Same fix applies to _plan_heal_or_flee when already at the trader."""

    def test_no_heal_item_at_trader_plans_buy(self):
        """HP low + no heal item + trader at same location → STEP_TRADE_BUY_ITEM."""
        agent = make_agent(hp=20, money=500, location_id="loc_a", inventory=[])
        state = make_state_with_trader(agent=agent, trader_at="loc_a")
        plan = _build_plan(agent, state, INTENT_HEAL_SELF)
        assert plan is not None
        assert plan.steps[0].kind == STEP_TRADE_BUY_ITEM
        assert plan.steps[0].payload.get("item_category") == "medical"


# ── Executor: buying water reduces money and adds water to inventory ───────────

class TestExecTradeBuyWater:
    """_exec_trade_buy: after execution the agent has water and less money."""

    def _run_buy(self, category: str = "drink", agent_id: str = "bot1"):
        from app.games.zone_stalkers.decision.models.plan import Plan, PlanStep
        agent = make_agent(thirst=80, money=500, location_id="loc_a")
        state = make_state_with_trader(agent=agent, trader_at="loc_a")
        # Ensure trader dict has an "id" field (required by _bot_buy_from_trader)
        for tid, tdata in state["traders"].items():
            tdata.setdefault("id", tid)
        state["agents"][agent_id] = agent

        ctx = build_agent_context(agent_id, agent, state)
        plan = Plan(
            intent_kind=INTENT_SEEK_WATER,
            steps=[PlanStep(
                kind=STEP_TRADE_BUY_ITEM,
                payload={"item_category": category},
                interruptible=False,
            )],
            confidence=1.0, created_turn=100,
        )
        events = execute_plan_step(ctx, plan, state, world_turn=100)
        return agent, state, events

    def test_buy_water_adds_item_to_inventory(self):
        """After executing STEP_TRADE_BUY_ITEM for 'drink', a drink item appears."""
        from app.games.zone_stalkers.balance.items import DRINK_ITEM_TYPES
        agent, state, events = self._run_buy(category="drink")
        drink_items = [i for i in agent.get("inventory", [])
                       if i.get("type") in DRINK_ITEM_TYPES]
        assert len(drink_items) >= 1, (
            "Expected at least one drink item in inventory after purchase"
        )

    def test_buy_water_deducts_money(self):
        """After executing STEP_TRADE_BUY_ITEM the agent has less money."""
        agent, state, events = self._run_buy(category="drink")
        assert agent["money"] < 500, (
            f"Expected money to decrease from 500; got {agent['money']}"
        )

    def test_buy_water_sets_action_used(self):
        """action_used must be True after the purchase."""
        agent, state, events = self._run_buy(category="drink")
        assert agent.get("action_used") is True


# ── Full pipeline integration: thirsty NPC at trader buys water ───────────────

class TestFullPipelineThirstyAtTrader:
    """End-to-end: intent selection → plan → executor with thirsty NPC at trader."""

    def test_full_pipeline_thirsty_npc_plans_buy(self):
        """Full pipeline: thirst=80 + trader at same loc → intent seek_water + buy plan."""
        agent = make_agent(
            thirst=80, money=500, location_id="loc_a",
            has_weapon=True, has_armor=True, has_ammo=True,
            inventory=[],  # empty: no water in inventory → should buy
        )
        state = make_state_with_trader(agent=agent, trader_at="loc_a")
        ctx = build_agent_context("bot1", agent, state)
        needs = evaluate_needs(ctx, state)
        intent = select_intent(ctx, needs, 100)
        assert intent.kind == INTENT_SEEK_WATER, (
            f"Expected seek_water intent; got {intent.kind!r}"
        )
        plan = build_plan(ctx, intent, state, 100)
        assert plan.steps[0].kind == STEP_TRADE_BUY_ITEM, (
            f"Thirsty NPC at trader should plan to buy, got {plan.steps[0].kind!r}"
        )
