"""Tests for context_builder knowledge-first wrappers (PR3)."""
from __future__ import annotations
from typing import Any
import uuid

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.knowledge.knowledge_store import (
    upsert_known_npc,
    upsert_known_trader,
    upsert_known_hazard,
)
from app.games.zone_stalkers.memory.store import ensure_memory_v3


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


def test_context_builder_prefers_knowledge_entities_without_memory_merge_when_knowledge_present():
    agent = _bare_agent("bot1", location_id="loc_A")
    target = _bare_agent("target_1", location_id="loc_X")
    from_memory = _bare_agent("memory_only", location_id="loc_M")

    upsert_known_npc(
        agent, other_agent_id="target_1", name="Target",
        location_id="loc_K", world_turn=480,
        source="direct_observation", confidence=0.95,
    )

    mem = ensure_memory_v3(agent)
    rec_id = str(uuid.uuid4())
    mem["records"][rec_id] = {
        "id": rec_id,
        "kind": "stalkers_seen",
        "title": "memory only entity",
        "summary": "memory entity",
        "created_turn": 490,
        "status": "active",
        "location_id": "loc_M",
        "entity_ids": ["memory_only"],
        "details": {"other_agent_id": "memory_only"},
        "tags": [],
    }

    state = _mk_state(
        agents={"bot1": agent, "target_1": target, "memory_only": from_memory},
        locations={
            "loc_A": {"id": "loc_A", "name": "Start"},
            "loc_K": {"id": "loc_K", "name": "Known"},
            "loc_M": {"id": "loc_M", "name": "Memory"},
        },
    )
    ctx = build_agent_context("bot1", agent, state)
    ids = {e["agent_id"] for e in ctx.known_entities}
    assert "target_1" in ids
    assert "memory_only" not in ids


def test_context_builder_keeps_memory_target_lead_when_knowledge_has_unrelated_npc():
    agent = _bare_agent("bot1", location_id="loc_A")
    agent["kill_target_id"] = "target_1"
    unrelated = _bare_agent("unrelated_1", location_id="loc_U")
    target = _bare_agent("target_1", location_id="loc_T")

    upsert_known_npc(
        agent, other_agent_id="unrelated_1", name="Unrelated",
        location_id="loc_U", world_turn=480,
        source="direct_observation", confidence=0.8,
    )

    mem = ensure_memory_v3(agent)
    rec_id = str(uuid.uuid4())
    mem["records"][rec_id] = {
        "id": rec_id,
        "kind": "target_last_known_location",
        "title": "target lead",
        "summary": "target in memory",
        "created_turn": 495,
        "status": "active",
        "location_id": "loc_T",
        "entity_ids": ["target_1"],
        "details": {"target_id": "target_1", "location_id": "loc_T"},
        "tags": ["target", "tracking"],
    }

    state = _mk_state(
        agents={"bot1": agent, "unrelated_1": unrelated, "target_1": target},
        locations={"loc_A": {"id": "loc_A", "name": "Start"}, "loc_T": {"id": "loc_T", "name": "Target Loc"}},
    )
    ctx = build_agent_context("bot1", agent, state)
    target_entry = next(e for e in ctx.known_entities if e["agent_id"] == "target_1")
    assert target_entry["last_known_location"] == "loc_T"


def test_context_builder_prefers_knowledge_locations_without_memory_merge_when_knowledge_present():
    agent = _bare_agent("bot1", location_id="loc_A")
    agent["knowledge_v1"] = {
        "revision": 1,
        "known_npcs": {},
        "known_locations": {
            "loc_K": {
                "location_id": "loc_K",
                "name": "Knowledge Loc",
                "last_visited_turn": 490,
                "confidence": 1.0,
            }
        },
        "known_traders": {},
        "known_hazards": {},
        "stats": {"known_npcs_count": 0, "detailed_known_npcs_count": 0, "last_update_turn": 490},
    }
    mem = ensure_memory_v3(agent)
    rec_id = str(uuid.uuid4())
    mem["records"][rec_id] = {
        "id": rec_id,
        "kind": "travel_hop",
        "title": "to memory loc",
        "summary": "travel",
        "created_turn": 495,
        "status": "active",
        "location_id": "loc_M",
        "entity_ids": [],
        "details": {"to_location_id": "loc_M"},
        "tags": ["travel"],
    }

    state = _mk_state(
        agents={"bot1": agent},
        locations={
            "loc_A": {"id": "loc_A", "name": "Start"},
            "loc_K": {"id": "loc_K", "name": "Knowledge Loc"},
            "loc_M": {"id": "loc_M", "name": "Memory Loc"},
        },
    )
    ctx = build_agent_context("bot1", agent, state)
    loc_ids = {loc["location_id"] for loc in ctx.known_locations}
    assert "loc_K" in loc_ids
    assert "loc_M" not in loc_ids


def test_context_builder_visible_trader_does_not_suppress_memory_traders():
    agent = _bare_agent("bot1", location_id="loc_A")
    mem = ensure_memory_v3(agent)
    rec_id = str(uuid.uuid4())
    mem["records"][rec_id] = {
        "id": rec_id,
        "kind": "trader_visited",
        "title": "met trader",
        "summary": "memory trader",
        "created_turn": 480,
        "status": "active",
        "location_id": "loc_B",
        "entity_ids": ["trader_memory"],
        "details": {"trader_id": "trader_memory", "trader_name": "Memory Trader", "location_id": "loc_B"},
        "tags": ["trader"],
    }

    visible_trader_agent = {
        "name": "Visible Trader",
        "location_id": "loc_A",
        "is_alive": True,
        "archetype": "trader_agent",
    }
    state = _mk_state(
        agents={"bot1": agent, "trader_visible": visible_trader_agent},
        locations={"loc_A": {"id": "loc_A", "name": "Start"}, "loc_B": {"id": "loc_B", "name": "Bar"}},
        traders={"trader_memory": {"name": "Memory Trader", "location_id": "loc_B"}},
    )
    ctx = build_agent_context("bot1", agent, state)
    trader_ids = {t.get("agent_id") for t in ctx.known_traders}
    assert "trader_visible" in trader_ids
    assert "trader_memory" in trader_ids


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
