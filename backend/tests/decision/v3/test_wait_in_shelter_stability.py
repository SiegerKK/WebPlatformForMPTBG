"""Fix 5 — WAIT_IN_SHELTER brain must remain stable until emission ends.

Tests that _brain_valid_until_turn returns the right horizon for
WAIT_IN_SHELTER and REACH_SAFE_SHELTER objective keys.
"""
from __future__ import annotations

from app.games.zone_stalkers.rules.tick_rules import _brain_valid_until_turn


def _agent_with_objective(key: str) -> dict:
    return {
        "brain_v3_context": {"objective_key": key},
        "scheduled_action": None,
    }


def test_wait_in_shelter_uses_emission_ends_turn() -> None:
    agent = _agent_with_objective("WAIT_IN_SHELTER")
    state = {"emission_ends_turn": 150}
    result = _brain_valid_until_turn(agent, state, world_turn=100)
    # Should be min(150, 100+30) = 130
    assert result == 130


def test_wait_in_shelter_caps_at_plus_30() -> None:
    agent = _agent_with_objective("WAIT_IN_SHELTER")
    # emission ends far in the future → cap at world_turn + 30
    state = {"emission_ends_turn": 9999}
    result = _brain_valid_until_turn(agent, state, world_turn=100)
    assert result == 130  # world_turn + 30


def test_wait_in_shelter_fallback_when_no_emission_end() -> None:
    agent = _agent_with_objective("WAIT_IN_SHELTER")
    state = {}  # no emission_ends_turn
    result = _brain_valid_until_turn(agent, state, world_turn=100)
    assert result == 105  # world_turn + 5


def test_reach_safe_shelter_returns_current_turn() -> None:
    agent = _agent_with_objective("REACH_SAFE_SHELTER")
    state = {"emission_ends_turn": 200}
    result = _brain_valid_until_turn(agent, state, world_turn=100)
    # REACH_SAFE_SHELTER re-evaluates every turn
    assert result == 100
