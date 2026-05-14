from __future__ import annotations

import uuid

import app.games.zone_stalkers.decision.context_builder as context_builder_module
from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.knowledge.knowledge_store import (
    ensure_knowledge_v1,
    upsert_known_corpse,
    upsert_known_hazard,
    upsert_known_location,
    upsert_known_npc,
    upsert_known_trader,
)
from app.games.zone_stalkers.memory.store import ensure_memory_v3
from tests.decision.conftest import make_agent, make_minimal_state


def test_context_builder_uses_known_npcs_without_memory_scan(monkeypatch) -> None:
    agent = make_agent(location_id="loc_a")
    state = make_minimal_state(agent=agent)
    target = make_agent(agent_id="target_1", location_id="loc_z")
    state["agents"]["target_1"] = target

    upsert_known_npc(
        agent,
        other_agent_id="target_1",
        name="Цель",
        location_id="loc_b",
        world_turn=99,
        source="direct_observation",
        confidence=0.95,
        observed_agent=target,
    )
    upsert_known_location(agent, location_id="loc_b", name="Локация Б", world_turn=99)
    state["traders"] = {"trader_1": {"name": "Сидорович", "location_id": "loc_b", "is_alive": True}}
    upsert_known_trader(agent, trader_id="trader_1", location_id="loc_b", world_turn=99, name="Сидорович")
    upsert_known_hazard(agent, location_id="loc_b", kind="anomaly_detected", world_turn=99, confidence=0.8)

    monkeypatch.setattr(
        context_builder_module,
        "_memory_v3_records",
        lambda _: (_ for _ in ()).throw(AssertionError("memory_v3 should not be scanned")),
    )

    ctx = build_agent_context("bot1", agent, state)
    target_entry = next(entry for entry in ctx.known_entities if entry["agent_id"] == "target_1")
    assert target_entry["last_known_location"] == "loc_b"
    assert agent["brain_context_metrics"]["context_builder_knowledge_primary_hits"] >= 1


def test_context_builder_uses_known_traders_without_memory_scan(monkeypatch) -> None:
    agent = make_agent(location_id="loc_a")
    state = make_minimal_state(agent=agent)
    state["traders"] = {
        "trader_1": {"name": "Сидорович", "location_id": "loc_b", "is_alive": True},
    }
    state["agents"]["other_1"] = make_agent(agent_id="other_1", location_id="loc_b")
    upsert_known_npc(
        agent,
        other_agent_id="other_1",
        name="Other",
        location_id="loc_b",
        world_turn=99,
        source="direct_observation",
        confidence=0.9,
        observed_agent=state["agents"]["other_1"],
    )
    upsert_known_location(agent, location_id="loc_b", name="Локация Б", world_turn=99)
    upsert_known_trader(agent, trader_id="trader_1", location_id="loc_b", world_turn=99, name="Сидорович")
    upsert_known_hazard(agent, location_id="loc_b", kind="anomaly_detected", world_turn=99, confidence=0.8)

    monkeypatch.setattr(
        context_builder_module,
        "_memory_v3_records",
        lambda _: (_ for _ in ()).throw(AssertionError("memory_v3 should not be scanned")),
    )

    ctx = build_agent_context("bot1", agent, state)
    assert {entry["agent_id"] for entry in ctx.known_traders} == {"trader_1"}


def test_context_builder_uses_known_corpses_for_corpse_leads() -> None:
    agent = make_agent(location_id="loc_a", kill_target_id="target_1")
    state = make_minimal_state(agent=agent)
    state["agents"]["target_1"] = make_agent(agent_id="target_1", location_id="loc_d")

    upsert_known_corpse(
        agent,
        corpse_id="corpse_target_1",
        dead_agent_id="target_1",
        dead_agent_name="Цель",
        location_id="loc_d",
        world_turn=99,
    )

    build_agent_context("bot1", agent, state)
    cache = agent["brain_context_cache"]["derived"]
    assert cache["corpse_leads"]
    assert cache["corpse_leads"][0]["location_id"] == "loc_d"


def test_context_builder_memory_fallback_for_legacy_agent() -> None:
    agent = make_agent(location_id="loc_a")
    state = make_minimal_state(agent=agent)
    state["agents"]["other_1"] = make_agent(agent_id="other_1", location_id="loc_b")

    mem = ensure_memory_v3(agent)
    rec_id = str(uuid.uuid4())
    mem["records"][rec_id] = {
        "id": rec_id,
        "kind": "stalkers_seen",
        "created_turn": 99,
        "status": "active",
        "location_id": "loc_b",
        "entity_ids": ["other_1"],
        "details": {"other_agent_id": "other_1", "location_id": "loc_b"},
    }

    ctx = build_agent_context("bot1", agent, state)
    assert {entry["agent_id"] for entry in ctx.known_entities} == {"other_1"}
    assert agent["brain_context_metrics"]["context_builder_memory_fallbacks"] >= 1


def test_context_builder_cache_not_invalidated_by_minor_observation_refresh() -> None:
    agent = make_agent(location_id="loc_a")
    state = make_minimal_state(agent=agent)
    state["world_turn"] = 500
    target = make_agent(agent_id="other_1", location_id="loc_b")
    state["agents"]["other_1"] = target

    upsert_known_npc(
        agent,
        other_agent_id="other_1",
        name="Other",
        location_id="loc_b",
        world_turn=500,
        source="direct_observation",
        confidence=0.9,
        observed_agent=target,
    )

    build_agent_context("bot1", agent, state)
    hits_before = agent["brain_context_metrics"]["context_builder_cache_hits"]
    misses_before = agent["brain_context_metrics"]["context_builder_cache_misses"]

    state["world_turn"] = 501
    upsert_known_npc(
        agent,
        other_agent_id="other_1",
        name="Other",
        location_id="loc_b",
        world_turn=501,
        source="direct_observation",
        confidence=0.9,
        observed_agent=target,
    )
    build_agent_context("bot1", agent, state)

    assert agent["brain_context_metrics"]["context_builder_cache_hits"] == hits_before + 1
    assert agent["brain_context_metrics"]["context_builder_cache_misses"] == misses_before


def test_context_builder_cache_invalidated_by_major_location_change() -> None:
    agent = make_agent(location_id="loc_a")
    state = make_minimal_state(agent=agent)
    state["world_turn"] = 500
    target = make_agent(agent_id="other_1", location_id="loc_b")
    state["agents"]["other_1"] = target

    upsert_known_npc(
        agent,
        other_agent_id="other_1",
        name="Other",
        location_id="loc_b",
        world_turn=500,
        source="direct_observation",
        confidence=0.9,
        observed_agent=target,
    )

    build_agent_context("bot1", agent, state)
    misses_before = agent["brain_context_metrics"]["context_builder_cache_misses"]

    state["world_turn"] = 501
    upsert_known_npc(
        agent,
        other_agent_id="other_1",
        name="Other",
        location_id="loc_c",
        world_turn=501,
        source="direct_observation",
        confidence=0.9,
        observed_agent=target,
    )
    state["locations"]["loc_c"] = {
        "name": "Локация В",
        "terrain_type": "buildings",
        "anomaly_activity": 0,
        "connections": [{"to": "loc_a", "travel_time": 12}],
        "items": [],
        "agents": [],
    }
    build_agent_context("bot1", agent, state)

    assert agent["brain_context_metrics"]["context_builder_cache_misses"] == misses_before + 1


def test_context_builder_target_without_hunt_signal_still_uses_memory_fallback() -> None:
    agent = make_agent(location_id="loc_a", kill_target_id="target_1")
    state = make_minimal_state(agent=agent)
    state["agents"]["target_1"] = make_agent(agent_id="target_1", location_id="loc_z")

    knowledge = ensure_knowledge_v1(agent)
    knowledge["known_locations"] = {"loc_a": {"location_id": "loc_a", "last_visited_turn": 99, "confidence": 1.0}}
    mem = ensure_memory_v3(agent)
    mem["records"]["target_lead"] = {
        "id": "target_lead",
        "kind": "target_last_known_location",
        "created_turn": 99,
        "status": "active",
        "location_id": "loc_b",
        "entity_ids": ["target_1"],
        "details": {"target_id": "target_1", "location_id": "loc_b"},
    }

    build_agent_context("bot1", agent, state)
    cache = agent["brain_context_cache"]["derived"]
    assert cache["target_leads"]
    assert agent["brain_context_metrics"]["context_builder_memory_fallbacks"] >= 1


def test_context_builder_does_not_scan_memory_when_known_npcs_exist_but_no_hazards_or_traders(monkeypatch) -> None:
    agent = make_agent(location_id="loc_a")
    state = make_minimal_state(agent=agent)
    state["agents"]["other_1"] = make_agent(agent_id="other_1", location_id="loc_b")
    upsert_known_npc(
        agent,
        other_agent_id="other_1",
        name="Other",
        location_id="loc_b",
        world_turn=99,
        source="direct_observation",
        confidence=0.9,
        observed_agent=state["agents"]["other_1"],
    )

    monkeypatch.setattr(
        context_builder_module,
        "_memory_v3_records",
        lambda _: (_ for _ in ()).throw(AssertionError("memory_v3 should not be scanned")),
    )
    ctx = build_agent_context("bot1", agent, state)
    assert {entry["agent_id"] for entry in ctx.known_entities} == {"other_1"}
    assert agent["brain_context_metrics"]["context_builder_knowledge_primary_hits"] >= 1


def test_context_builder_cache_not_invalidated_by_unrelated_memory_revision_when_knowledge_sufficient() -> None:
    agent = make_agent(location_id="loc_a")
    state = make_minimal_state(agent=agent)
    state["world_turn"] = 500
    state["agents"]["other_1"] = make_agent(agent_id="other_1", location_id="loc_b")
    upsert_known_npc(
        agent,
        other_agent_id="other_1",
        name="Other",
        location_id="loc_b",
        world_turn=500,
        source="direct_observation",
        confidence=0.9,
        observed_agent=state["agents"]["other_1"],
    )

    build_agent_context("bot1", agent, state)
    hits_before = agent["brain_context_metrics"]["context_builder_cache_hits"]
    misses_before = agent["brain_context_metrics"]["context_builder_cache_misses"]

    ensure_memory_v3(agent)["stats"]["memory_revision"] = (
        int(ensure_memory_v3(agent)["stats"].get("memory_revision", 0)) + 1
    )
    state["world_turn"] = 501
    build_agent_context("bot1", agent, state)

    assert agent["brain_context_metrics"]["context_builder_cache_hits"] == hits_before + 1
    assert agent["brain_context_metrics"]["context_builder_cache_misses"] == misses_before
