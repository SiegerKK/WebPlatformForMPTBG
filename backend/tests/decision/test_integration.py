"""Integration tests: full decision pipeline with complete state scenarios."""
from __future__ import annotations

import pytest

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.needs import evaluate_needs
from app.games.zone_stalkers.decision.intents import select_intent
from app.games.zone_stalkers.decision.planner import build_plan
from app.games.zone_stalkers.decision.models.plan import (
    STEP_CONSUME_ITEM,
    STEP_TRADE_SELL_ITEM,
    STEP_TRAVEL_TO_LOCATION,
    STEP_WAIT,
    STEP_EXPLORE_LOCATION,
)
from tests.decision.conftest import make_agent, make_minimal_state, make_state_with_trader


def _run_pipeline(agent_id, agent, state):
    """Run the full v2 decision pipeline and return (intent, plan)."""
    ctx = build_agent_context(agent_id, agent, state)
    needs = evaluate_needs(ctx, state)
    intent = select_intent(ctx, needs, state.get("world_turn", 100))
    plan = build_plan(ctx, intent, state, state.get("world_turn", 100))
    return intent, plan


class TestIntegrationScenarios:
    def test_hungry_agent_eats_from_inventory(self):
        """НПЦ при голоде>=70 выбирает еду из инвентаря.

        Agent is rich + equipped so only hunger score (0.75) is relevant vs
        other zeroed-out scores → seek_food wins.
        """
        from app.games.zone_stalkers.balance.items import FOOD_ITEM_TYPES
        food_type = next(iter(FOOD_ITEM_TYPES))
        agent = make_agent(
            hunger=75, money=9000, material_threshold=3000,
            has_weapon=True, has_armor=True, has_ammo=True,
            inventory=[{"type": food_type, "value": 30}],
        )
        state = make_minimal_state(agent=agent)
        intent, plan = _run_pipeline("bot1", agent, state)
        assert intent.kind == "seek_food"
        assert plan.steps[0].kind == STEP_CONSUME_ITEM

    def test_low_hp_agent_heals(self):
        """НПЦ при HP<=28 выбирает лечение."""
        from app.games.zone_stalkers.balance.items import HEAL_ITEM_TYPES
        heal_type = next(iter(HEAL_ITEM_TYPES))
        agent = make_agent(
            hp=25, money=9000, material_threshold=3000,
            has_weapon=True, has_armor=True, has_ammo=True,
            inventory=[{"type": heal_type, "value": 100}],
        )
        state = make_minimal_state(agent=agent)
        intent, plan = _run_pipeline("bot1", agent, state)
        # heal_self: (50-25)/(50-20)=25/30~0.83 beats survive_now: (30-25)/(30-10)=0.25
        assert intent.kind in ("heal_self", "escape_danger")
        assert plan.steps[0].kind == STEP_CONSUME_ITEM

    def test_emission_dangerous_terrain_flees(self):
        """НПЦ при выбросе на опасной местности убегает."""
        agent = make_agent()
        state = make_minimal_state(agent=agent, loc_terrain="plain")
        state["emission_active"] = True
        # loc_b is buildings (safe)
        intent, plan = _run_pipeline("bot1", agent, state)
        assert intent.kind == "flee_emission"
        assert plan.steps[0].kind == STEP_TRAVEL_TO_LOCATION

    def test_emission_safe_terrain_waits(self):
        """НПЦ при выбросе на безопасной местности ждёт."""
        agent = make_agent()
        state = make_minimal_state(agent=agent, loc_terrain="buildings")
        state["emission_active"] = True
        intent, plan = _run_pipeline("bot1", agent, state)
        assert intent.kind == "wait_in_shelter"
        assert plan.steps[0].kind == STEP_WAIT

    def test_rich_agent_with_artifacts_sells(self):
        """Богатый НПЦ с артефактами едет продавать."""
        from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
        artifact_type = next(iter(ARTIFACT_TYPES))
        agent = make_agent(
            money=9000, material_threshold=3000,
            hp=100, hunger=0, thirst=0, sleepiness=0,
            has_weapon=True, has_armor=True, has_ammo=True,
            inventory=[{"type": artifact_type, "value": 500}],
        )
        state = make_state_with_trader(agent=agent, trader_at="loc_a")
        intent, plan = _run_pipeline("bot1", agent, state)
        assert intent.kind == "sell_artifacts"
        assert plan.steps[0].kind == STEP_TRADE_SELL_ITEM

    def test_poor_agent_goes_to_anomaly(self):
        """Бедный НПЦ идёт исследовать аномалию (loc_b has anomaly_activity=5)."""
        agent = make_agent(money=0, material_threshold=3000,
                           has_weapon=True, has_armor=True, has_ammo=True)
        state = make_minimal_state(agent=agent)
        # loc_a has no anomaly (default 0), loc_b has anomaly_activity=5
        intent, plan = _run_pipeline("bot1", agent, state)
        assert intent.kind == "get_rich"
        assert plan.steps[0].kind in (STEP_TRAVEL_TO_LOCATION, STEP_EXPLORE_LOCATION)

    def test_hunter_pursues_target_over_get_rich(self):
        """Убийца (rich) prefers hunt_target over get_rich (no artifacts)."""
        agent = make_agent(
            global_goal="kill_stalker", kill_target_id="target_1",
            money=3000, material_threshold=3000,
            hp=100, hunger=0, thirst=0, sleepiness=0,
            has_weapon=True, has_armor=True, has_ammo=True,
        )
        state = make_state_with_trader(agent=agent, trader_at="loc_a")
        state["agents"]["target_1"] = make_agent(agent_id="target_1", location_id="loc_b")
        intent, plan = _run_pipeline("bot1", agent, state)
        # hunt_target=0.8 > trade=0.0 (no artifacts) → INTENT_HUNT_TARGET
        assert intent.kind == "hunt_target"

    def test_global_goal_achieved_routes_to_exit(self):
        """НПЦ с выполненной целью ищет выход."""
        agent = make_agent(global_goal_achieved=True, money=9000, material_threshold=3000,
                           has_weapon=True, has_armor=True, has_ammo=True)
        state = make_minimal_state(agent=agent)
        state["locations"]["loc_b"]["exit_zone"] = True
        intent, plan = _run_pipeline("bot1", agent, state)
        assert intent.kind == "leave_zone"
        assert plan.steps[0].kind == STEP_TRAVEL_TO_LOCATION
        assert plan.steps[0].payload["target_id"] == "loc_b"
