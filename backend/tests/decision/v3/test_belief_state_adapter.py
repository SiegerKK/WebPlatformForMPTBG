"""Tests for BeliefState adapter (PR 3)."""
from __future__ import annotations

import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from tests.decision.conftest import make_agent, make_state_with_trader, make_minimal_state
from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.beliefs import (
    build_belief_state,
    find_trader_location_from_beliefs,
    find_water_source_from_beliefs,
    find_food_source_from_beliefs,
)
from app.games.zone_stalkers.memory.models import MemoryRecord, LAYER_SEMANTIC, LAYER_SPATIAL, LAYER_EPISODIC
from app.games.zone_stalkers.memory.store import add_memory_record, ensure_memory_v3


def test_build_belief_state_has_required_fields() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)
    belief = build_belief_state(ctx, agent, world_turn=100)
    assert belief.agent_id == "bot1"
    assert belief.location_id == "loc_a"
    assert isinstance(belief.visible_entities, tuple)
    assert isinstance(belief.known_traders, tuple)
    assert isinstance(belief.known_items, tuple)
    assert isinstance(belief.known_threats, tuple)
    assert isinstance(belief.relevant_memories, tuple)
    assert isinstance(belief.confidence_summary, dict)


def test_known_trader_from_context_appears_in_belief() -> None:
    agent = make_agent(location_id="loc_b")
    state = make_state_with_trader(agent=agent, trader_at="loc_b")
    ctx = build_agent_context("bot1", agent, state)
    belief = build_belief_state(ctx, agent, world_turn=100)
    trader_ids = [t.get("agent_id") for t in belief.known_traders]
    assert "trader_1" in trader_ids


def test_known_trader_from_memory_v3_appears_in_belief() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)
    # Add a semantic memory record about a trader.
    rec = MemoryRecord(
        id="mem_trader_sem",
        agent_id="bot1",
        layer=LAYER_SEMANTIC,
        kind="trader_location_known",
        created_turn=50,
        last_accessed_turn=None,
        summary="Гнидорович в Бункере",
        details={"trader_id": "gnid_1", "trader_name": "Гнидорович"},
        location_id="loc_bunker",
        tags=("trader", "trade"),
        importance=0.8,
        confidence=0.9,
    )
    add_memory_record(agent, rec)
    ctx = build_agent_context("bot1", agent, state)
    belief = build_belief_state(ctx, agent, world_turn=100)
    trader_ids = [t.get("agent_id") for t in belief.known_traders]
    assert "gnid_1" in trader_ids


def test_stale_record_excluded_from_belief_by_default() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)
    rec = MemoryRecord(
        id="mem_stale_trader",
        agent_id="bot1",
        layer=LAYER_SEMANTIC,
        kind="trader_location_known",
        created_turn=1,
        last_accessed_turn=None,
        summary="old trader",
        details={"trader_id": "old_trader"},
        location_id="loc_x",
        tags=("trader",),
        importance=0.5,
        confidence=0.5,
        status="stale",
    )
    add_memory_record(agent, rec)
    ctx = build_agent_context("bot1", agent, state)
    belief = build_belief_state(ctx, agent, world_turn=100)
    trader_ids = [t.get("agent_id") for t in belief.known_traders]
    assert "old_trader" not in trader_ids


def test_find_trader_location_from_beliefs() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)
    rec = MemoryRecord(
        id="mem_t1",
        agent_id="bot1",
        layer=LAYER_SEMANTIC,
        kind="trader_location_known",
        created_turn=50,
        last_accessed_turn=None,
        summary="trader",
        details={"trader_id": "t1"},
        location_id="loc_b",
        tags=("trader",),
        confidence=0.9,
    )
    add_memory_record(agent, rec)
    ctx = build_agent_context("bot1", agent, state)
    belief = build_belief_state(ctx, agent, world_turn=100)
    loc = find_trader_location_from_beliefs(belief, agent, world_turn=100)
    assert loc == "loc_b"


def test_confidence_summary_counts() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)
    ctx = build_agent_context("bot1", agent, state)
    belief = build_belief_state(ctx, agent, world_turn=100)
    cs = belief.confidence_summary
    assert "records_total" in cs
    assert "active" in cs
    assert "stale" in cs
    assert "archived" in cs
