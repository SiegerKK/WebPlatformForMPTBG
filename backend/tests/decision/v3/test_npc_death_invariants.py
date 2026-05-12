"""Fix 1 — Canonical kill_agent invariants.

Tests that after kill_agent (or _mark_agent_dead) the agent dict satisfies
all death-state invariants:

  1.  is_alive == False
  2.  hp == 0
  3.  current_goal == "dead"
  4.  brain_runtime cleared (key absent or None)
  5.  brain_v3_context cleared (key absent or None)
"""
from __future__ import annotations

import copy

from app.games.zone_stalkers.rules.agent_lifecycle import kill_agent


def _make_alive_agent(agent_id: str = "npc_1") -> dict:
    return {
        "id": agent_id,
        "name": "Test Stalker",
        "archetype": "stalker_agent",
        "controller": {"kind": "bot"},
        "is_alive": True,
        "hp": 100,
        "max_hp": 100,
        "location_id": "loc_a",
        "current_goal": "explore",
        "hunger": 20,
        "thirst": 20,
        "radiation": 0,
        "sleepiness": 0,
        "money": 500,
        "inventory": [],
        "equipment": {},
        "scheduled_action": {"type": "move_to", "target_location_id": "loc_b"},
        "action_queue": [{"type": "move_to", "target_location_id": "loc_c"}],
        "brain_runtime": {
            "valid_until_turn": 99,
            "objective_key": "FIND_ARTIFACTS",
        },
        "brain_v3_context": {
            "objective_key": "FIND_ARTIFACTS",
            "objective_score": 0.8,
        },
        "memory_v3": None,
        "faction": "loner",
        "global_goal": "get_rich",
    }


def test_kill_agent_sets_is_alive_false() -> None:
    agent = _make_alive_agent()
    state: dict = {"locations": {"loc_a": {"agents": ["npc_1"]}}, "agents": {"npc_1": agent}}
    kill_agent(agent_id="npc_1", agent=agent, state=state, cause="test", world_turn=10)
    assert agent["is_alive"] is False


def test_kill_agent_sets_hp_zero() -> None:
    agent = _make_alive_agent()
    state: dict = {"locations": {"loc_a": {"agents": ["npc_1"]}}, "agents": {"npc_1": agent}}
    kill_agent(agent_id="npc_1", agent=agent, state=state, cause="test", world_turn=10)
    assert agent["hp"] == 0


def test_kill_agent_sets_current_goal_dead() -> None:
    agent = _make_alive_agent()
    state: dict = {"locations": {"loc_a": {"agents": ["npc_1"]}}, "agents": {"npc_1": agent}}
    kill_agent(agent_id="npc_1", agent=agent, state=state, cause="test", world_turn=10)
    assert agent.get("current_goal") == "dead"


def test_kill_agent_clears_brain_runtime() -> None:
    """brain_runtime must be invalidated (not necessarily removed) after death."""
    agent = _make_alive_agent()
    state: dict = {"locations": {"loc_a": {"agents": ["npc_1"]}}, "agents": {"npc_1": agent}}
    assert agent.get("brain_runtime") is not None
    kill_agent(agent_id="npc_1", agent=agent, state=state, cause="test", world_turn=10)
    br = agent.get("brain_runtime") or {}
    # Brain must be tombstoned: valid_until_turn == death turn, skip_reason == dead
    assert br.get("last_skip_reason") == "dead"
    assert br.get("valid_until_turn") == 10


def test_kill_agent_clears_brain_v3_context() -> None:
    """brain_v3_context must have objective_key=None and intent_kind=None after death."""
    agent = _make_alive_agent()
    state: dict = {"locations": {"loc_a": {"agents": ["npc_1"]}}, "agents": {"npc_1": agent}}
    assert agent.get("brain_v3_context") is not None
    kill_agent(agent_id="npc_1", agent=agent, state=state, cause="test", world_turn=10)
    ctx = agent.get("brain_v3_context") or {}
    assert ctx.get("objective_key") is None
    assert ctx.get("intent_kind") is None
