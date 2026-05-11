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


def _full_supplies():
    """Inventory with all supplies at the minimum level for risk_tolerance=0.5.

    Provides 3 ammo items, 2 food, 2 drink, 3 medicine so that
    ``reload_or_rearm`` returns 0.0 for a weapon+armor equipped agent.
    """
    return [
        {"id": "ammo_0", "type": "ammo_9mm", "value": 60},
        {"id": "ammo_1", "type": "ammo_9mm", "value": 60},
        {"id": "ammo_2", "type": "ammo_9mm", "value": 60},
        {"id": "food_0", "type": "bread", "value": 40},
        {"id": "food_1", "type": "bread", "value": 40},
        {"id": "drink_0", "type": "water", "value": 30},
        {"id": "drink_1", "type": "water", "value": 30},
        {"id": "med_0", "type": "bandage", "value": 50},
        {"id": "med_1", "type": "bandage", "value": 50},
        {"id": "med_2", "type": "bandage", "value": 50},
    ]


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
    def test_no_weapon_only_armor_present_is_065(self):
        """No weapon but armor is present → 0.65 (weapon gap drives score)."""
        agent = make_agent(has_weapon=False, has_armor=True, material_threshold=0)
        needs = _needs_for(agent=agent)
        assert needs.reload_or_rearm == 0.65

    def test_no_armor_is_0_7(self):
        agent = make_agent(has_weapon=True, has_armor=False, has_ammo=False, material_threshold=0)
        needs = _needs_for(agent=agent)
        assert abs(needs.reload_or_rearm - 0.7) < 1e-9

    def test_has_weapon_and_armor_and_no_ammo(self):
        """With weapon=pistol+armor but 0 ammo → ammo gap score ≈ 0.60 > 0.5."""
        agent = make_agent(has_weapon=True, has_armor=True, has_ammo=False, material_threshold=0)
        needs = _needs_for(agent=agent)
        assert needs.reload_or_rearm > 0.5

    def test_fully_equipped_and_stocked(self):
        """weapon + armor + 3 ammo + 2 food + 2 drink + 3 medicine → 0.0."""
        agent = make_agent(has_weapon=True, has_armor=True, has_ammo=False, material_threshold=0)
        agent["inventory"] = _full_supplies()
        needs = _needs_for(agent=agent)
        assert needs.reload_or_rearm == 0.0

    def test_no_food_stock_triggers_resupply(self):
        """Agent with weapon+armor+ammo but no food → reload_or_rearm = 0.55."""
        agent = make_agent(has_weapon=True, has_armor=True, has_ammo=False, material_threshold=0)
        # Only ammo and drink/medicine in inventory (no food)
        agent["inventory"] = [
            {"id": "ammo_0", "type": "ammo_9mm", "value": 60},
            {"id": "ammo_1", "type": "ammo_9mm", "value": 60},
            {"id": "ammo_2", "type": "ammo_9mm", "value": 60},
            {"id": "drink_0", "type": "water", "value": 30},
            {"id": "drink_1", "type": "water", "value": 30},
            {"id": "med_0", "type": "bandage", "value": 50},
            {"id": "med_1", "type": "bandage", "value": 50},
            {"id": "med_2", "type": "bandage", "value": 50},
        ]
        needs = _needs_for(agent=agent)
        assert abs(needs.reload_or_rearm - 0.55) < 1e-9

    def test_no_drink_stock_triggers_resupply(self):
        """Agent with weapon+armor+ammo but no drink → reload_or_rearm = 0.55."""
        agent = make_agent(has_weapon=True, has_armor=True, has_ammo=False, material_threshold=0)
        agent["inventory"] = [
            {"id": "ammo_0", "type": "ammo_9mm", "value": 60},
            {"id": "ammo_1", "type": "ammo_9mm", "value": 60},
            {"id": "ammo_2", "type": "ammo_9mm", "value": 60},
            {"id": "food_0", "type": "bread", "value": 40},
            {"id": "food_1", "type": "bread", "value": 40},
            {"id": "med_0", "type": "bandage", "value": 50},
            {"id": "med_1", "type": "bandage", "value": 50},
            {"id": "med_2", "type": "bandage", "value": 50},
        ]
        needs = _needs_for(agent=agent)
        assert abs(needs.reload_or_rearm - 0.55) < 1e-9

    def test_ammo_count_below_3_triggers_resupply(self):
        """Agent with 1 ammo item (< DESIRED_AMMO_COUNT=3) → reload_or_rearm ≈ 0.40."""
        agent = make_agent(has_weapon=True, has_armor=True, has_ammo=False, material_threshold=0)
        agent["inventory"] = [
            {"id": "ammo_0", "type": "ammo_9mm", "value": 60},
            # Only 1 ammo item — also add food/drink/medicine to isolate ammo score
            {"id": "food_0", "type": "bread", "value": 40},
            {"id": "food_1", "type": "bread", "value": 40},
            {"id": "drink_0", "type": "water", "value": 30},
            {"id": "drink_1", "type": "water", "value": 30},
            {"id": "med_0", "type": "bandage", "value": 50},
            {"id": "med_1", "type": "bandage", "value": 50},
            {"id": "med_2", "type": "bandage", "value": 50},
        ]
        needs = _needs_for(agent=agent)
        # ammo_count=1/3 → 0.60*(1-1/3) = 0.40
        assert abs(needs.reload_or_rearm - 0.40) < 1e-9

    def test_medicine_below_desired_triggers_resupply(self):
        """Agent fully equipped (weapon+armor+3 ammo+2 food+2 drink) but no medicine → 0.45."""
        agent = make_agent(has_weapon=True, has_armor=True, has_ammo=False)
        agent["inventory"] = [
            {"id": "ammo_0", "type": "ammo_9mm", "value": 60},
            {"id": "ammo_1", "type": "ammo_9mm", "value": 60},
            {"id": "ammo_2", "type": "ammo_9mm", "value": 60},
            {"id": "food_0", "type": "bread", "value": 40},
            {"id": "food_1", "type": "bread", "value": 40},
            {"id": "drink_0", "type": "water", "value": 30},
            {"id": "drink_1", "type": "water", "value": 30},
        ]
        needs = _needs_for(agent=agent)
        assert abs(needs.reload_or_rearm - 0.45) < 1e-9

    def test_risk_tolerance_affects_desired_food_count(self):
        """Low risk tolerance (0.0) requires 3 food items; high (1.0) requires only 1."""
        # Low risk tolerance: needs 3 food, has 2 → still below desired
        agent_cautious = make_agent(has_weapon=True, has_armor=True, has_ammo=False)
        agent_cautious["risk_tolerance"] = 0.0
        agent_cautious["inventory"] = [
            {"id": "ammo_0", "type": "ammo_9mm", "value": 60},
            {"id": "ammo_1", "type": "ammo_9mm", "value": 60},
            {"id": "ammo_2", "type": "ammo_9mm", "value": 60},
            {"id": "food_0", "type": "bread", "value": 40},
            {"id": "food_1", "type": "bread", "value": 40},  # 2 food < 3 desired
            {"id": "drink_0", "type": "water", "value": 30},
            {"id": "drink_1", "type": "water", "value": 30},
            {"id": "drink_2", "type": "water", "value": 30},
            {"id": "med_0", "type": "bandage", "value": 50},
            {"id": "med_1", "type": "bandage", "value": 50},
            {"id": "med_2", "type": "bandage", "value": 50},
            {"id": "med_3", "type": "bandage", "value": 50},
        ]
        needs_cautious = _needs_for(agent=agent_cautious)
        assert needs_cautious.reload_or_rearm > 0.0  # food gap

        # High risk tolerance: needs only 1 food, has 2 → OK
        agent_risky = make_agent(has_weapon=True, has_armor=True, has_ammo=False)
        agent_risky["risk_tolerance"] = 1.0
        agent_risky["inventory"] = [
            {"id": "ammo_0", "type": "ammo_9mm", "value": 60},
            {"id": "ammo_1", "type": "ammo_9mm", "value": 60},
            {"id": "ammo_2", "type": "ammo_9mm", "value": 60},
            {"id": "food_0", "type": "bread", "value": 40},  # 1 food == 1 desired
            {"id": "drink_0", "type": "water", "value": 30},  # 1 drink == 1 desired
            {"id": "med_0", "type": "bandage", "value": 50},
            {"id": "med_1", "type": "bandage", "value": 50},  # 2 med == 2 desired
        ]
        needs_risky = _needs_for(agent=agent_risky)
        assert needs_risky.reload_or_rearm == 0.0  # all needs met for risk_tolerance=1.0


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
    def test_zero_liquid_wealth_fully_equipped_max_score(self):
        """Fully stocked agent with zero liquid wealth → get_rich = 0.70 (no suppression).

        All supplies at target: weapon+armor+3 ammo+2 food+2 drink+3 medicine.
        Item values are set to 0 so that liquid wealth stays at exactly 0 for a
        clean formula check.
        """
        agent = make_agent(money=0, material_threshold=0,
                           has_weapon=True, has_armor=True, has_ammo=False)
        agent["inventory"] = [{**i, "value": 0} for i in _full_supplies()]
        needs = _needs_for(agent=agent)
        assert needs.reload_or_rearm == 0.0, "should be fully stocked"
        assert abs(needs.get_rich - 0.70) < 1e-9

    def test_at_threshold_zero(self):
        agent = make_agent(money=3000, material_threshold=3000)
        needs = _needs_for(agent=agent)
        assert needs.get_rich == 0.0

    def test_half_threshold_fully_stocked(self):
        """money=1500, fully stocked, upgrade available → get_rich suppressed by upgrade score."""
        agent = make_agent(money=1500, material_threshold=3000,
                           has_weapon=True, has_armor=True, has_ammo=False)
        agent["inventory"] = [{**i, "value": 0} for i in _full_supplies()]
        needs = _needs_for(agent=agent)
        # The agent can afford a weapon upgrade (pistol→shotgun at 1200 coins)
        # so reload_or_rearm = 0.25 (upgrade score), which suppresses get_rich.
        # get_rich = (1-0.5) * 0.70 * (1-reload_or_rearm)
        expected_get_rich = 0.5 * 0.70 * (1.0 - needs.reload_or_rearm)
        assert abs(needs.get_rich - expected_get_rich) < 1e-9


# ── Equipment gate on get_rich ────────────────────────────────────────────────

class TestEquipmentGateOnGetRich:
    def test_no_weapon_suppresses_get_rich(self):
        """Missing weapon (reload_or_rearm=0.65) suppresses get_rich below resupply score.

        Agent has armor but no weapon.  The weapon gap (0.65) drives the score.
        """
        agent = make_agent(money=0, material_threshold=0,
                           has_weapon=False, has_armor=True, has_ammo=False)
        needs = _needs_for(agent=agent)
        # reload_or_rearm = 0.65 (weapon gap); suppression = (1 - 0.65) = 0.35
        assert abs(needs.reload_or_rearm - 0.65) < 1e-9
        # base get_rich = 0.70; suppressed: 0.70 * 0.35 = 0.245
        assert abs(needs.get_rich - 0.70 * 0.35) < 1e-9
        assert needs.get_rich < needs.reload_or_rearm

    def test_no_armor_suppresses_get_rich(self):
        """Missing armor (reload_or_rearm=0.70) suppresses get_rich below resupply score."""
        agent = make_agent(money=0, material_threshold=0,
                           has_weapon=True, has_armor=False, has_ammo=False)
        agent["inventory"] = [{"type": "ammo_9mm", "quantity": 20, "value": 0}]
        needs = _needs_for(agent=agent)
        # reload_or_rearm=0.70; suppression factor=0.30; base get_rich=0.70
        assert abs(needs.get_rich - 0.70 * 0.30) < 1e-9
        assert needs.get_rich < needs.reload_or_rearm

    def test_no_ammo_suppresses_get_rich(self):
        """Missing ammo (reload_or_rearm=0.60) suppresses get_rich below resupply score."""
        agent = make_agent(money=0, material_threshold=0,
                           has_weapon=True, has_armor=True, has_ammo=False)
        needs = _needs_for(agent=agent)
        # reload_or_rearm=0.60; suppression factor=0.40; base get_rich=0.70
        assert abs(needs.get_rich - 0.70 * 0.40) < 1e-9
        assert needs.get_rich < needs.reload_or_rearm

    def test_fully_stocked_no_suppression(self):
        """Fully stocked agent → reload_or_rearm=0 → get_rich is not suppressed."""
        agent = make_agent(money=0, material_threshold=0,
                           has_weapon=True, has_armor=True, has_ammo=False)
        agent["inventory"] = [{**i, "value": 0} for i in _full_supplies()]
        needs = _needs_for(agent=agent)
        assert needs.reload_or_rearm == 0.0
        assert abs(needs.get_rich - 0.70) < 1e-9

    def test_equipment_value_excluded_from_wealth_ratio(self):
        """Equipment value is NOT counted toward liquid wealth for get_rich."""
        # Agent has weapon (value=300) + armor (value=200) but zero money/inventory.
        # Old behaviour: wealth=500, ratio=500/3000=0.167, get_rich=0.583.
        # New behaviour: liquid_wealth=0, ratio=0, base get_rich=0.70 (then suppressed).
        agent = make_agent(money=0, material_threshold=0,
                           has_weapon=True, has_armor=True, has_ammo=False)
        needs = _needs_for(agent=agent)
        # reload_or_rearm=0.60 (ammo gap), liquid_wealth=0 → base=0.70, suppressed: 0.70*0.40=0.28
        assert abs(needs.get_rich - 0.70 * 0.40) < 1e-9

    def test_phase1_reload_or_rearm_excludes_blocked_equipment(self):
        """Phase-1 non-hunter keeps get_rich active despite weapon gap."""
        agent = make_agent(
            money=100,
            material_threshold=3000,
            has_weapon=False,
            has_armor=True,
            has_ammo=False,
            global_goal="get_rich",
        )
        needs = _needs_for(agent=agent)
        assert needs.reload_or_rearm == 0.0
        assert needs.get_rich > 0.0


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


# ── Fix 1: global_goal_achieved zeroes goal-driven drives ────────────────────

class TestGlobalGoalAchievedZerosDrives:
    def test_hunt_target_zeroed_when_goal_achieved(self):
        """hunt_target must be 0.0 when global_goal_achieved=True (Fix 1)."""
        agent = make_agent(
            global_goal="kill_stalker",
            kill_target_id="target_1",
            money=3000, material_threshold=3000,
            has_weapon=True, has_armor=True, has_ammo=True,
            global_goal_achieved=True,
        )
        state = make_minimal_state(agent_id="bot1", agent=agent)
        state["agents"]["target_1"] = make_agent(agent_id="target_1", location_id="loc_b")
        needs = _needs_for(agent=agent, state=state)
        assert needs.hunt_target == 0.0, (
            f"hunt_target should be 0.0 after goal achieved, got {needs.hunt_target}"
        )

    def test_unravel_zeroed_when_goal_achieved(self):
        """unravel_zone_mystery must be 0.0 when global_goal_achieved=True (Fix 1)."""
        agent = make_agent(
            global_goal="unravel_zone_mystery",
            money=3000, material_threshold=3000,
            has_weapon=True, has_armor=True, has_ammo=True,
            global_goal_achieved=True,
        )
        needs = _needs_for(agent=agent)
        assert needs.unravel_zone_mystery == 0.0, (
            f"unravel_zone_mystery should be 0.0 after goal achieved, got {needs.unravel_zone_mystery}"
        )

    def test_leave_zone_boosted_when_goal_achieved_and_still_in_zone(self):
        """leave_zone >= 0.8 when global_goal_achieved=True and has_left_zone=False (Fix 1)."""
        agent = make_agent(
            global_goal="get_rich",
            money=9000, material_threshold=3000,
            has_weapon=True, has_armor=True, has_ammo=True,
            global_goal_achieved=True,
        )
        # has_left_zone defaults to False in make_agent
        needs = _needs_for(agent=agent)
        assert needs.leave_zone >= 0.8, (
            f"leave_zone should be >= 0.8 after goal achieved (still in zone), got {needs.leave_zone}"
        )


# ── Fix 2: multiplicative suppression for risky drives under survival pressure ─

class TestSurvivalPressureSuppression:
    def test_get_rich_suppressed_when_survive_now_high(self):
        """get_rich is suppressed when survive_now is high (Fix 2)."""
        # HP=5 → survive_now=1.0 → _survival_pressure=1.0
        # get_rich base=0.35 (half wealth) → after suppression: 0.35 * (1 - 1.0) = 0.0
        agent = make_agent(
            hp=5,
            money=1500, material_threshold=3000,
            has_weapon=True, has_armor=True, has_ammo=True,
        )
        needs = _needs_for(agent=agent)
        assert needs.get_rich == 0.0, (
            f"get_rich should be 0.0 under max survival pressure, got {needs.get_rich}"
        )

    def test_get_rich_partially_suppressed_when_heal_self_moderate(self):
        """get_rich is partially suppressed when heal_self is moderate (Fix 2)."""
        # HP=35 → heal_self=0.5 → _survival_pressure = max(survive_now, 0.5*0.5) = max(0.0, 0.25) = 0.25
        # wealth=0 → get_rich base = 0.70 → after: 0.70 * (1 - 0.25) = 0.525
        agent = make_agent(
            hp=35,
            money=0, material_threshold=3000,
            has_weapon=True, has_armor=True, has_ammo=True,
        )
        needs = _needs_for(agent=agent)
        assert needs.get_rich < 0.70, (
            f"get_rich should be suppressed when heal_self is moderate, got {needs.get_rich}"
        )
        assert needs.get_rich > 0.0, "get_rich should not be fully suppressed at moderate HP"
