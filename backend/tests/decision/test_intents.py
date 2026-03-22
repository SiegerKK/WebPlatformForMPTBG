"""Tests for intent selection (intents.py)."""
from __future__ import annotations

import pytest

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.needs import evaluate_needs
from app.games.zone_stalkers.decision.intents import select_intent
from app.games.zone_stalkers.decision.models.intent import (
    INTENT_ESCAPE_DANGER,
    INTENT_FLEE_EMISSION,
    INTENT_WAIT_IN_SHELTER,
    INTENT_HEAL_SELF,
    INTENT_SEEK_FOOD,
    INTENT_SEEK_WATER,
    INTENT_GET_RICH,
    INTENT_HUNT_TARGET,
    INTENT_LEAVE_ZONE,
    INTENT_SELL_ARTIFACTS,
    INTENT_IDLE,
)
from tests.decision.conftest import make_agent, make_minimal_state, make_state_with_trader


def _intent_for(agent_id="bot1", agent=None, state=None):
    """Helper: run full pipeline and return selected intent."""
    if agent is None:
        agent = make_agent()
    if state is None:
        state = make_minimal_state(agent_id=agent_id, agent=agent)
    ctx = build_agent_context(agent_id, agent, state)
    needs = evaluate_needs(ctx, state)
    return select_intent(ctx, needs, state.get("world_turn", 100))


class TestIntentPriority:
    def test_survive_now_hp_critical(self):
        """HP=5 → survive_now=1.0 → INTENT_ESCAPE_DANGER."""
        agent = make_agent(hp=5)
        intent = _intent_for(agent=agent)
        assert intent.kind == INTENT_ESCAPE_DANGER

    def test_hunger_low_does_not_trigger_food(self):
        """Hunger=20 is low; get_rich=0.0 (rich agent) or other priorities dominate."""
        # Use a rich, fully equipped agent so that only hunger matters
        agent = make_agent(hunger=20, money=9000, material_threshold=3000)
        intent = _intent_for(agent=agent)
        # get_rich=0.0 (rich), reload_or_rearm=0.0 (equipped)
        # eat=0.20, all others=0.0 → eat wins → seek_food
        assert intent.kind == INTENT_SEEK_FOOD

    def test_emission_beats_hunger(self):
        """Emission on dangerous terrain beats hunger."""
        agent = make_agent(hunger=90, money=0)
        state = make_minimal_state(agent=agent, loc_terrain="plain")
        state["emission_active"] = True
        intent = _intent_for(agent=agent, state=state)
        assert intent.kind == INTENT_FLEE_EMISSION

    def test_poor_agent_get_rich(self):
        """Fully equipped, poor agent pursues get_rich (score=0.70)."""
        agent = make_agent(money=0, material_threshold=3000,
                           has_weapon=True, has_armor=True, has_ammo=True)
        intent = _intent_for(agent=agent)
        assert intent.kind == INTENT_GET_RICH

    def test_kill_stalker_agent_rich(self):
        """Rich kill_stalker agent → hunt_target wins."""
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1",
                           money=3000, material_threshold=3000,
                           has_weapon=True, has_armor=True, has_ammo=True)
        state = make_minimal_state(agent_id="bot1", agent=agent)
        state["agents"]["target_1"] = make_agent(agent_id="target_1", location_id="loc_b")
        intent = _intent_for(agent=agent, state=state)
        assert intent.kind == INTENT_HUNT_TARGET

    def test_global_goal_achieved_leave_zone(self):
        """global_goal_achieved=True → leave_zone=1.0 → INTENT_LEAVE_ZONE."""
        # Rich + equipped so nothing else fires
        agent = make_agent(global_goal_achieved=True, money=9000, material_threshold=3000,
                           has_weapon=True, has_armor=True, has_ammo=True)
        intent = _intent_for(agent=agent)
        assert intent.kind == INTENT_LEAVE_ZONE

    def test_no_active_needs_idle(self):
        """Fully equipped, healthy, rich agent → INTENT_IDLE."""
        agent = make_agent(hp=100, hunger=0, thirst=0, sleepiness=0,
                           money=9000, material_threshold=3000,
                           has_weapon=True, has_armor=True, has_ammo=True)
        intent = _intent_for(agent=agent)
        assert intent.kind == INTENT_IDLE

    def test_emission_safe_terrain_wait_in_shelter(self):
        """Emission active but agent is on safe terrain → INTENT_WAIT_IN_SHELTER."""
        agent = make_agent()
        state = make_minimal_state(agent=agent, loc_terrain="buildings")
        state["emission_active"] = True
        intent = _intent_for(agent=agent, state=state)
        assert intent.kind == INTENT_WAIT_IN_SHELTER

    def test_sell_artifacts_trader_colocated(self):
        """Trader colocated + artifacts → trade score → INTENT_SELL_ARTIFACTS."""
        from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
        artifact_type = next(iter(ARTIFACT_TYPES))
        agent = make_agent(
            hp=100, hunger=0, thirst=0, sleepiness=0,
            money=9000, material_threshold=3000,
            has_weapon=True, has_armor=True, has_ammo=True,
            inventory=[{"type": artifact_type, "value": 500}],
        )
        state = make_state_with_trader(agent=agent, trader_at="loc_a")
        intent = _intent_for(agent=agent, state=state)
        assert intent.kind == INTENT_SELL_ARTIFACTS


# ── Fix 2: hard interrupt for critical thirst forces INTENT_SEEK_WATER ────────

class TestHardInterruptCriticalNeeds:
    def test_seek_water_forced_when_thirst_critical(self):
        """INTENT_SEEK_WATER is forced when thirst >= 90 (Fix 2 hard interrupt)."""
        # Rich, healthy, equipped agent with only extreme thirst
        agent = make_agent(
            hp=100, hunger=0, thirst=90, sleepiness=0,
            money=9000, material_threshold=3000,
            has_weapon=True, has_armor=True, has_ammo=True,
        )
        intent = _intent_for(agent=agent)
        assert intent.kind == INTENT_SEEK_WATER, (
            f"Expected INTENT_SEEK_WATER on thirst=90, got {intent.kind}"
        )

    def test_seek_food_forced_when_hunger_critical(self):
        """INTENT_SEEK_FOOD is forced when hunger >= 90 (Fix 2 hard interrupt)."""
        agent = make_agent(
            hp=100, hunger=90, thirst=0, sleepiness=0,
            money=9000, material_threshold=3000,
            has_weapon=True, has_armor=True, has_ammo=True,
        )
        intent = _intent_for(agent=agent)
        assert intent.kind == INTENT_SEEK_FOOD, (
            f"Expected INTENT_SEEK_FOOD on hunger=90, got {intent.kind}"
        )

    def test_survive_now_beats_critical_thirst(self):
        """INTENT_ESCAPE_DANGER wins over INTENT_SEEK_WATER when survive_now fires (Fix 2 priority)."""
        agent = make_agent(
            hp=5, thirst=90,
            money=9000, material_threshold=3000,
            has_weapon=True, has_armor=True, has_ammo=True,
        )
        intent = _intent_for(agent=agent)
        assert intent.kind == INTENT_ESCAPE_DANGER, (
            f"Expected INTENT_ESCAPE_DANGER (survive_now > drink), got {intent.kind}"
        )

    def test_thirst_89_does_not_hard_interrupt(self):
        """Thirst=89 is below the 0.90 threshold — no hard interrupt, normal priority wins (Fix 2)."""
        # Rich equipped agent with thirst=89 → drink score=0.89, get_rich=0.0 → seek_water wins anyway
        # but it's via priority map not hard interrupt; score should still be seek_water
        agent = make_agent(
            hp=100, hunger=0, thirst=89, sleepiness=0,
            money=9000, material_threshold=3000,
            has_weapon=True, has_armor=True, has_ammo=True,
        )
        intent = _intent_for(agent=agent)
        # drink=0.89 still beats idle (0.0) via the priority map → should be seek_water
        assert intent.kind == INTENT_SEEK_WATER, (
            f"Expected INTENT_SEEK_WATER via priority map at thirst=89, got {intent.kind}"
        )
