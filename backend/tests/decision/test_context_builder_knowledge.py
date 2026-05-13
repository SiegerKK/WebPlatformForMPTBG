"""Tests for context_builder knowledge-first wrappers (PR3)."""
from __future__ import annotations
from typing import Any
import pytest
from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.knowledge.knowledge_store import (
    ensure_knowledge_v1,
    upsert_known_npc,
    upsert_known_location,
    upsert_known_trader,
    upsert_known_hazard,
)


def _mk_state(
    agents: dict | None = None,
    locations: dict | None = None,
    traders: dict | None = None,
    world_turn: int = 500,
) -> dict[str, Any]:
    return {
        "world_turn": world_turn,
        "world_day": 1,
        "world_hour": 12,
        "world_minute": 0,
        "agents": agents or {},
        "locations": locations or {},
        "traders": traders or {},
    }


def _bare_agent(agent_id: str, location_id: str = "loc_A") -> dict[str, Any]:
    return {
        "name": agent_id,
        "location_id": location_id,
        "is_alive": True,
        "memory_v3": None,
        "knowledge_v1": None,
    }


def test_context_builder_uses_known_npcs_for_target_location():
    """When knowledge_v1 has a known target location, context includes it."""
    agent = _bare_agent("bot1", location_id="loc_A")
    agent["kill_target_id"] = "target_1"
    target = _bare_agent("target_1", location_id="loc_B")
    target["archetype"] = "stalker"

    # Add knowledge about target
    upsert_known_npc(
        agent, other_agent_id="target_1", name="Цель",
        location_id="loc_C", world_turn=480,
        source="direct_observation", confidence=0.9,
    )

    state = _mk_state(
        agents={"bot1": agent, "target_1": target},
        locations={
            "loc_A": {"id": "loc_A", "name": "Bunker"},
            "loc_B": {"id": "loc_B", "name": "Factory"},
            "loc_C": {"id": "loc_C", "name": "Swamp"},
        },
        world_turn=500,
    )

    ctx = build_agent_context("bot1", agent, state)
    entity_ids = {e["agent_id"] for e in ctx.known_entities}
    assert "target_1" in entity_ids

    target_entry = next(e for e in ctx.known_entities if e["agent_id"] == "target_1")
    # Knowledge says loc_C, not the live location loc_B
    assert target_entry.get("last_known_location") == "loc_C"


def test_context_builder_falls_back_to_memory_v3_when_knowledge_missing():
    """Without knowledge_v1, context_builder falls back to memory_v3 records."""
    from app.games.zone_stalkers.memory.store import ensure_memory_v3
    import uuid

    agent = _bare_agent("bot1", location_id="loc_A")
    target = _bare_agent("other_1", location_id="loc_X")
    target["archetype"] = "stalker"

    # Write a memory_v3 record that mentions other_1
    mem = ensure_memory_v3(agent)
    rec_id = str(uuid.uuid4())
    mem["records"][rec_id] = {
        "id": rec_id,
        "kind": "stalkers_seen",
        "title": "saw stalker",
        "summary": "test",
        "created_turn": 490,
        "status": "active",
        "location_id": "loc_X",
        "entity_ids": ["other_1"],
        "details": {"other_agent_id": "other_1"},
        "tags": [],
    }
    # DO NOT upsert knowledge_v1 — should fall back

    state = _mk_state(
        agents={"bot1": agent, "other_1": target},
        locations={
            "loc_A": {"id": "loc_A", "name": "Start"},
            "loc_X": {"id": "loc_X", "name": "Remote"},
        },
        world_turn=500,
    )

    ctx = build_agent_context("bot1", agent, state)
    entity_ids = {e["agent_id"] for e in ctx.known_entities}
    assert "other_1" in entity_ids


def test_context_builder_known_hazards_from_knowledge():
    """Known hazards from knowledge_v1 appear in context.known_hazards."""
    agent = _bare_agent("bot1")
    upsert_known_hazard(agent, location_id="loc_D6", kind="emission_death",
                        world_turn=490, confidence=0.85)

    state = _mk_state(
        agents={"bot1": agent},
        locations={"loc_A": {"id": "loc_A", "name": "Start"},
                   "loc_D6": {"id": "loc_D6", "name": "Dark Valley 6"}},
        world_turn=500,
    )
    ctx = build_agent_context("bot1", agent, state)
    haz_ids = {h.get("location_id") for h in ctx.known_hazards}
    assert "loc_D6" in haz_ids


def test_context_builder_known_traders_from_knowledge():
    """Known traders from knowledge_v1 appear in context.known_traders."""
    agent = _bare_agent("bot1")
    upsert_known_trader(agent, trader_id="trader_sidor", location_id="loc_Bar",
                        world_turn=400, name="Sidorovich", confidence=1.0)

    state = _mk_state(
        agents={"bot1": agent},
        locations={"loc_A": {"id": "loc_A", "name": "Start"}},
        traders={"trader_sidor": {"name": "Sidorovich", "location_id": "loc_Bar"}},
        world_turn=500,
    )
    ctx = build_agent_context("bot1", agent, state)
    trader_ids = {t.get("agent_id") for t in ctx.known_traders}
    assert "trader_sidor" in trader_ids


def test_context_builder_returns_agent_context_type():
    """build_agent_context always returns an AgentContext even for empty state."""
    from app.games.zone_stalkers.decision.models.agent_context import AgentContext

    agent = _bare_agent("bot1")
    state = _mk_state(agents={"bot1": agent})
    ctx = build_agent_context("bot1", agent, state)
    assert isinstance(ctx, AgentContext)
    assert ctx.agent_id == "bot1"
