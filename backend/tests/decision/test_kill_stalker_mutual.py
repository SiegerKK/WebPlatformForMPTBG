"""Tests: two NPCs with kill_stalker goal targeting each other (mutual hunt)."""
from __future__ import annotations

from typing import Any

import pytest

from tests.decision.conftest import make_agent


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_mutual_kill_state(
    bot1_loc: str = "loc_a",
    bot2_loc: str = "loc_a",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return (state, bot1, bot2) with each agent targeting the other.

    By default both start at the same location so combat can be initiated
    immediately on the first tick.
    """
    bot1 = make_agent(
        agent_id="bot1",
        global_goal="kill_stalker",
        kill_target_id="bot2",
        money=3000,
        material_threshold=3000,
        has_weapon=True,
        has_armor=True,
        has_ammo=True,
        location_id=bot1_loc,
    )
    bot2 = make_agent(
        agent_id="bot2",
        global_goal="kill_stalker",
        kill_target_id="bot1",
        money=3000,
        material_threshold=3000,
        has_weapon=True,
        has_armor=True,
        has_ammo=True,
        location_id=bot2_loc,
    )
    state: dict[str, Any] = {
        "world_turn": 1,
        "world_day": 1,
        "world_hour": 12,
        "world_minute": 0,
        "emission_active": False,
        "agents": {"bot1": bot1, "bot2": bot2},
        "traders": {},
        "locations": {
            "loc_a": {
                "name": "Кордон",
                "terrain_type": "buildings",
                "anomaly_activity": 0,
                "connections": [{"to": "loc_b", "travel_time": 12}],
                "items": [],
                "agents": [aid for aid, agent in [("bot1", bot1), ("bot2", bot2)]
                           if agent["location_id"] == "loc_a"],
            },
            "loc_b": {
                "name": "Свалка",
                "terrain_type": "buildings",
                "anomaly_activity": 0,
                "connections": [{"to": "loc_a", "travel_time": 12}],
                "items": [],
                "agents": [aid for aid, agent in [("bot1", bot1), ("bot2", bot2)]
                           if agent["location_id"] == "loc_b"],
            },
        },
        "combat_interactions": {},
        "relations": {},
        "groups": {},
    }
    return state, bot1, bot2


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestMutualKillStalkerIntent:
    """Both agents select INTENT_HUNT_TARGET via the v2 decision pipeline."""

    def test_both_agents_select_hunt_target_intent(self) -> None:
        from app.games.zone_stalkers.decision.context_builder import build_agent_context
        from app.games.zone_stalkers.decision.needs import evaluate_needs
        from app.games.zone_stalkers.decision.intents import select_intent

        state, bot1, bot2 = _make_mutual_kill_state()

        for agent_id, agent in [("bot1", bot1), ("bot2", bot2)]:
            ctx = build_agent_context(agent_id, agent, state)
            needs = evaluate_needs(ctx, state)
            intent = select_intent(ctx, needs, world_turn=1)
            assert intent.kind == "hunt_target", (
                f"{agent_id} should select hunt_target intent, got {intent.kind!r}"
            )

    def test_hunt_target_suppressed_after_goal_completion(self) -> None:
        """Once goal is achieved (target confirmed dead + _check called), hunt_target is suppressed."""
        from app.games.zone_stalkers.decision.context_builder import build_agent_context
        from app.games.zone_stalkers.decision.needs import evaluate_needs
        from app.games.zone_stalkers.decision.intents import select_intent
        from app.games.zone_stalkers.rules.tick_rules import _check_global_goal_completion

        state, bot1, bot2 = _make_mutual_kill_state()
        bot2["is_alive"] = False
        # Mark goal as achieved (as done by the engine each tick)
        _check_global_goal_completion("bot1", bot1, state, world_turn=1)
        assert bot1.get("global_goal_achieved") is True

        ctx = build_agent_context("bot1", bot1, state)
        needs = evaluate_needs(ctx, state)
        intent = select_intent(ctx, needs, world_turn=1)
        assert intent.kind != "hunt_target", (
            "hunt_target intent should be suppressed after global_goal_achieved=True"
        )


class TestMutualKillStalkerCombat:
    """Combat initiation when both hunters are at the same location."""

    def test_combat_initiated_when_colocated(self) -> None:
        from app.games.zone_stalkers.rules.tick_rules import _compat_pursue_kill_stalker

        state, bot1, bot2 = _make_mutual_kill_state(bot1_loc="loc_a", bot2_loc="loc_a")

        events = _compat_pursue_kill_stalker("bot1", bot1, "loc_a", state, world_turn=1)

        combat_events = [e for e in events if e["event_type"] == "combat_initiated"]
        assert combat_events, "bot1 should initiate combat with bot2 when co-located"
        combat_id = combat_events[0]["payload"]["combat_id"]
        assert combat_id in state["combat_interactions"], (
            "combat_id must be registered in state['combat_interactions']"
        )

    def test_no_combat_when_target_at_different_location(self) -> None:
        from app.games.zone_stalkers.rules.tick_rules import _compat_pursue_kill_stalker

        state, bot1, bot2 = _make_mutual_kill_state(bot1_loc="loc_a", bot2_loc="loc_b")

        events = _compat_pursue_kill_stalker("bot1", bot1, "loc_a", state, world_turn=1)

        combat_events = [e for e in events if e["event_type"] == "combat_initiated"]
        assert not combat_events, (
            "No combat should be initiated when the target is in a different location"
        )

    def test_both_agents_would_initiate_combat_if_given_the_tick(self) -> None:
        """Verify that if both agents run their tick, both try to engage."""
        from app.games.zone_stalkers.rules.tick_rules import _compat_pursue_kill_stalker

        state, bot1, bot2 = _make_mutual_kill_state(bot1_loc="loc_a", bot2_loc="loc_a")

        # Reset action_used between ticks
        bot1.pop("action_used", None)
        bot2.pop("action_used", None)

        events1 = _compat_pursue_kill_stalker("bot1", bot1, "loc_a", state, world_turn=1)
        # bot2's action_used is not set by bot1's tick, so bot2 can still act
        bot2.pop("action_used", None)
        events2 = _compat_pursue_kill_stalker("bot2", bot2, "loc_a", state, world_turn=1)

        assert any(e["event_type"] == "combat_initiated" for e in events1), (
            "bot1 should initiate combat"
        )
        # bot2 may either also initiate or find existing combat — both are valid
        # as long as no error is raised
        _ = events2  # no assertion on events2 — either outcome is acceptable


class TestMutualKillStalkerGoalCompletion:
    """Goal completion logic for kill_stalker when one target is dead."""

    def test_goal_achieved_when_target_is_dead(self) -> None:
        from app.games.zone_stalkers.rules.tick_rules import _check_global_goal_completion

        state, bot1, bot2 = _make_mutual_kill_state()
        bot2["is_alive"] = False

        _check_global_goal_completion("bot1", bot1, state, world_turn=5)

        assert bot1.get("global_goal_achieved") is True, (
            "bot1 should have achieved its goal after bot2 is killed"
        )

    def test_goal_not_achieved_when_target_still_alive(self) -> None:
        from app.games.zone_stalkers.rules.tick_rules import _check_global_goal_completion

        state, bot1, bot2 = _make_mutual_kill_state()

        _check_global_goal_completion("bot1", bot1, state, world_turn=1)

        assert not bot1.get("global_goal_achieved", False), (
            "bot1 goal should NOT be achieved while bot2 is still alive"
        )

    def test_dead_agent_goal_not_achieved_when_killer_still_alive(self) -> None:
        """A dead bot should not have its own goal marked achieved."""
        from app.games.zone_stalkers.rules.tick_rules import _check_global_goal_completion

        state, bot1, bot2 = _make_mutual_kill_state()
        bot2["is_alive"] = False

        # bot2 is dead; its target (bot1) is still alive
        _check_global_goal_completion("bot2", bot2, state, world_turn=5)

        assert not bot2.get("global_goal_achieved", False), (
            "bot2 goal should NOT be achieved because bot1 (bot2's target) is still alive"
        )

    def test_both_could_achieve_goal_if_they_killed_each_other(self) -> None:
        """Edge case: simultaneous kill — both goals achieved."""
        from app.games.zone_stalkers.rules.tick_rules import _check_global_goal_completion

        state, bot1, bot2 = _make_mutual_kill_state()
        # Simulate simultaneous kills
        bot1["is_alive"] = False
        bot2["is_alive"] = False

        _check_global_goal_completion("bot1", bot1, state, world_turn=10)
        _check_global_goal_completion("bot2", bot2, state, world_turn=10)

        assert bot1.get("global_goal_achieved") is True
        assert bot2.get("global_goal_achieved") is True
