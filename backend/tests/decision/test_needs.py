"""Tests for NeedScores formulas (needs.py)."""
from __future__ import annotations

import pytest

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.needs import evaluate_needs, _score_survive_now, _score_heal_self
from tests.decision.conftest import make_agent, make_minimal_state, make_state_with_trader


def _needs_for(agent_id="bot1", agent=None, state=None):
    """Helper: build context + evaluate needs for a given agent/state."""
    if agent is None:
        agent = make_agent()
    if state is None:
        state = make_minimal_state(agent_id=agent_id, agent=agent)
    ctx = build_agent_context(agent_id, agent, state)
    return evaluate_needs(ctx, state)


# ── survive_now ───────────────────────────────────────────────────────────────

class TestSurviveNow:
    def test_full_hp(self):
        assert _score_survive_now(100) == 0.0

    def test_hp_30_boundary(self):
        assert _score_survive_now(30) == 0.0

    def test_hp_20_midpoint(self):
        # hp=20: (30-20)/(30-10) = 10/20 = 0.5
        assert abs(_score_survive_now(20) - 0.5) < 1e-9

    def test_hp_10_max(self):
        assert _score_survive_now(10) == 1.0

    def test_hp_5_max(self):
        assert _score_survive_now(5) == 1.0

    def test_hp_0_max(self):
        assert _score_survive_now(0) == 1.0

    def test_via_evaluate_needs_safe(self):
        agent = make_agent(hp=100)
        needs = _needs_for(agent=agent)
        assert needs.survive_now == 0.0

    def test_via_evaluate_needs_critical(self):
        agent = make_agent(hp=5)
        needs = _needs_for(agent=agent)
        assert needs.survive_now == 1.0


# ── heal_self ─────────────────────────────────────────────────────────────────

class TestHealSelf:
    def test_full_hp(self):
        assert _score_heal_self(100) == 0.0

    def test_hp_50_boundary(self):
        assert _score_heal_self(50) == 0.0

    def test_hp_35_midpoint(self):
        # (50-35)/(50-20) = 15/30 = 0.5
        assert abs(_score_heal_self(35) - 0.5) < 1e-9

    def test_hp_20_max(self):
        assert _score_heal_self(20) == 1.0

    def test_hp_5_max(self):
        assert _score_heal_self(5) == 1.0


# ── eat ───────────────────────────────────────────────────────────────────────

class TestEat:
    def test_no_hunger(self):
        agent = make_agent(hunger=0)
        needs = _needs_for(agent=agent)
        assert needs.eat == 0.0

    def test_half_hunger(self):
        agent = make_agent(hunger=50)
        needs = _needs_for(agent=agent)
        assert abs(needs.eat - 0.5) < 1e-9

    def test_full_hunger(self):
        agent = make_agent(hunger=100)
        needs = _needs_for(agent=agent)
        assert needs.eat == 1.0


# ── drink ─────────────────────────────────────────────────────────────────────

class TestDrink:
    def test_no_thirst(self):
        agent = make_agent(thirst=0)
        needs = _needs_for(agent=agent)
        assert needs.drink == 0.0

    def test_half_thirst(self):
        agent = make_agent(thirst=50)
        needs = _needs_for(agent=agent)
        assert abs(needs.drink - 0.5) < 1e-9

    def test_full_thirst(self):
        agent = make_agent(thirst=100)
        needs = _needs_for(agent=agent)
        assert needs.drink == 1.0


# ── sleep ─────────────────────────────────────────────────────────────────────

class TestSleep:
    def test_no_sleepiness(self):
        agent = make_agent(sleepiness=0)
        needs = _needs_for(agent=agent)
        assert needs.sleep == 0.0

    def test_half_sleepiness(self):
        agent = make_agent(sleepiness=50)
        needs = _needs_for(agent=agent)
        assert abs(needs.sleep - 0.5) < 1e-9

    def test_full_sleepiness(self):
        agent = make_agent(sleepiness=100)
        needs = _needs_for(agent=agent)
        assert needs.sleep == 1.0


# ── reload_or_rearm ───────────────────────────────────────────────────────────

class TestReloadOrRearm:
    def test_no_weapon_is_1(self):
        agent = make_agent(has_weapon=False, has_armor=False)
        needs = _needs_for(agent=agent)
        assert needs.reload_or_rearm == 0.65

    def test_no_armor_is_0_7(self):
        agent = make_agent(has_weapon=True, has_armor=False, has_ammo=False)
        needs = _needs_for(agent=agent)
        assert abs(needs.reload_or_rearm - 0.7) < 1e-9

    def test_has_weapon_and_armor_and_no_ammo(self):
        """With weapon=pistol+armor but 0 ammo → ammo_score=0 → reload_or_rearm > 0.5."""
        agent = make_agent(has_weapon=True, has_armor=True, has_ammo=False)
        # Remove any ammo that was added by default (has_ammo defaults to True via make_agent
        # when has_weapon is True, but here has_ammo=False so none is added)
        needs = _needs_for(agent=agent)
        # pistol needs ammo_9mm, 0/20 = 0.0 < 0.5 → reload_or_rearm = clamp(1.0 - 0.0) = 1.0
        assert needs.reload_or_rearm > 0.5

    def test_fully_equipped(self):
        agent = make_agent(has_weapon=True, has_armor=True, has_ammo=True)
        needs = _needs_for(agent=agent)
        # 20 ammo_9mm → ammo_score=1.0 → not < 0.5 → 0.0
        assert needs.reload_or_rearm == 0.0


# ── avoid_emission ────────────────────────────────────────────────────────────

class TestAvoidEmission:
    def test_no_emission_safe_terrain(self):
        agent = make_agent()
        state = make_minimal_state(agent=agent, loc_terrain="buildings")
        needs = _needs_for(agent=agent, state=state)
        assert needs.avoid_emission == 0.0

    def test_emission_active_dangerous_terrain(self):
        agent = make_agent()
        state = make_minimal_state(agent=agent, loc_terrain="plain")
        state["emission_active"] = True
        needs = _needs_for(agent=agent, state=state)
        assert needs.avoid_emission == 1.0

    def test_emission_active_safe_terrain(self):
        agent = make_agent()
        state = make_minimal_state(agent=agent, loc_terrain="buildings")
        state["emission_active"] = True
        needs = _needs_for(agent=agent, state=state)
        assert abs(needs.avoid_emission - 0.3) < 1e-9


# ── get_rich ──────────────────────────────────────────────────────────────────

class TestGetRich:
    def test_zero_wealth_max_score(self):
        # money=0 + no inventory value = wealth=0 → get_rich=0.70
        agent = make_agent(money=0, material_threshold=3000,
                           has_weapon=False, has_armor=False, has_ammo=False)
        needs = _needs_for(agent=agent)
        assert abs(needs.get_rich - 0.70) < 1e-9

    def test_at_threshold_zero(self):
        agent = make_agent(money=3000, material_threshold=3000)
        needs = _needs_for(agent=agent)
        assert needs.get_rich == 0.0

    def test_half_threshold(self):
        # money=1500, no items → wealth=1500, ratio=0.5 → get_rich=(1-0.5)*0.7=0.35
        agent = make_agent(money=1500, material_threshold=3000,
                           has_weapon=False, has_armor=False, has_ammo=False)
        needs = _needs_for(agent=agent)
        assert abs(needs.get_rich - 0.35) < 1e-9


# ── hunt_target ───────────────────────────────────────────────────────────────

class TestHuntTarget:
    def test_no_kill_goal_zero(self):
        agent = make_agent(global_goal="get_rich")
        needs = _needs_for(agent=agent)
        assert needs.hunt_target == 0.0

    def test_kill_stalker_poor(self):
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1",
                           money=0, material_threshold=3000,
                           has_weapon=False, has_armor=False, has_ammo=False)
        state = make_minimal_state(agent_id="bot1", agent=agent)
        state["agents"]["target_1"] = make_agent(agent_id="target_1", location_id="loc_b")
        needs = _needs_for(agent=agent, state=state)
        # wealth_ratio=0 → hunt_target = 0.8 * max(0.25, 0) = 0.20
        assert abs(needs.hunt_target - 0.20) < 1e-9

    def test_kill_stalker_rich(self):
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1",
                           money=3000, material_threshold=3000)
        state = make_minimal_state(agent_id="bot1", agent=agent)
        state["agents"]["target_1"] = make_agent(agent_id="target_1", location_id="loc_b")
        needs = _needs_for(agent=agent, state=state)
        # wealth_ratio~1.0 → hunt_target = 0.8 * max(0.25, ~1.0) ~ 0.8
        assert needs.hunt_target > 0.7


# ── trade ─────────────────────────────────────────────────────────────────────

class TestTrade:
    def test_no_trader_colocated_zero(self):
        from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
        artifact_type = next(iter(ARTIFACT_TYPES))
        agent = make_agent(inventory=[{"type": artifact_type, "value": 500}])
        state = make_minimal_state(agent=agent)
        # No trader at loc_a
        needs = _needs_for(agent=agent, state=state)
        assert needs.trade == 0.0

    def test_trader_colocated_with_artifacts(self):
        from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
        artifact_type = next(iter(ARTIFACT_TYPES))
        agent = make_agent(inventory=[{"type": artifact_type, "value": 500}])
        state = make_state_with_trader(agent=agent, trader_at="loc_a")
        # Trader is at loc_a, same as agent
        needs = _needs_for(agent=agent, state=state)
        assert abs(needs.trade - 0.7) < 1e-9

    def test_trader_colocated_no_artifacts_zero(self):
        agent = make_agent()
        state = make_state_with_trader(agent=agent, trader_at="loc_a")
        needs = _needs_for(agent=agent, state=state)
        assert needs.trade == 0.0
