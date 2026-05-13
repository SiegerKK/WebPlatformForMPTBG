from __future__ import annotations

import uuid

import app.games.zone_stalkers.decision.context_builder as context_builder_module
from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.knowledge.knowledge_store import upsert_known_npc
from app.games.zone_stalkers.memory.store import ensure_memory_v3
from app.games.zone_stalkers.rules.tick_constants import CRITICAL_THIRST_THRESHOLD
from tests.decision.conftest import make_agent, make_minimal_state


def test_context_builder_cache_hit_for_same_revision_location_objective() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)

    build_agent_context("bot1", agent, state)
    metrics = agent["brain_context_metrics"]
    assert metrics["context_builder_cache_misses"] == 1
    assert metrics["context_builder_cache_hits"] == 0

    state["world_turn"] = 101
    build_agent_context("bot1", agent, state)
    metrics = agent["brain_context_metrics"]
    assert metrics["context_builder_cache_hits"] == 1
    assert metrics["context_builder_cache_misses"] == 1


def test_context_builder_cache_invalidates_on_knowledge_revision_change() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)
    state["agents"]["other_1"] = make_agent(agent_id="other_1", location_id="loc_b")

    build_agent_context("bot1", agent, state)
    upsert_known_npc(
        agent,
        other_agent_id="other_1",
        name="Other",
        location_id="loc_b",
        world_turn=state["world_turn"],
        source="direct_observation",
        confidence=0.9,
    )
    build_agent_context("bot1", agent, state)

    metrics = agent["brain_context_metrics"]
    assert metrics["context_builder_cache_misses"] == 2


def test_context_builder_cache_invalidates_on_location_change() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)

    build_agent_context("bot1", agent, state)
    agent["location_id"] = "loc_b"
    build_agent_context("bot1", agent, state)

    metrics = agent["brain_context_metrics"]
    assert metrics["context_builder_cache_misses"] == 2


def test_context_builder_cache_invalidates_on_objective_change() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)

    build_agent_context("bot1", agent, state)
    agent["active_objective"] = {"key": "KILL_STALKER"}
    build_agent_context("bot1", agent, state)

    metrics = agent["brain_context_metrics"]
    assert metrics["context_builder_cache_misses"] == 2


def test_context_builder_cache_invalidates_on_target_change() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)

    build_agent_context("bot1", agent, state)
    agent["kill_target_id"] = "target_1"
    build_agent_context("bot1", agent, state)

    metrics = agent["brain_context_metrics"]
    assert metrics["context_builder_cache_misses"] == 2


def test_context_builder_cache_bypassed_for_combat_active() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)

    build_agent_context("bot1", agent, state)
    state["combat_interactions"]["bot1"] = {"enemy_id": "target_1"}
    build_agent_context("bot1", agent, state)

    metrics = agent["brain_context_metrics"]
    assert metrics["context_builder_cache_hits"] == 0
    assert metrics["context_builder_cache_misses"] == 2


def test_context_builder_cache_bypassed_for_critical_survival_need() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)

    build_agent_context("bot1", agent, state)
    agent["thirst"] = CRITICAL_THIRST_THRESHOLD
    build_agent_context("bot1", agent, state)

    metrics = agent["brain_context_metrics"]
    assert metrics["context_builder_cache_hits"] == 0
    assert metrics["context_builder_cache_misses"] == 2


def test_cached_context_does_not_share_mutable_lists_with_agent_state() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)
    state["agents"]["other_1"] = make_agent(agent_id="other_1", location_id="loc_b")
    upsert_known_npc(
        agent,
        other_agent_id="other_1",
        name="Other",
        location_id="loc_b",
        world_turn=state["world_turn"],
        source="direct_observation",
        confidence=0.9,
    )

    first_ctx = build_agent_context("bot1", agent, state)
    assert any(entity["agent_id"] == "other_1" for entity in first_ctx.known_entities)

    second_ctx = build_agent_context("bot1", agent, state)
    second_ctx.known_entities.append({"agent_id": "mutated"})

    third_ctx = build_agent_context("bot1", agent, state)
    ids = {entity.get("agent_id") for entity in third_ctx.known_entities}
    assert "mutated" not in ids
    assert "other_1" in ids


def test_context_builder_scans_memory_once_on_cache_miss(monkeypatch) -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)
    state["agents"]["other_1"] = make_agent(agent_id="other_1", location_id="loc_b")

    mem = ensure_memory_v3(agent)
    rec_id = str(uuid.uuid4())
    mem["records"][rec_id] = {
        "id": rec_id,
        "kind": "target_seen",
        "title": "saw target",
        "summary": "target seen",
        "created_turn": 99,
        "status": "active",
        "location_id": "loc_b",
        "entity_ids": ["other_1"],
        "details": {"target_id": "other_1", "location_id": "loc_b"},
        "tags": ["target"],
    }

    original = context_builder_module._memory_v3_records
    calls = {"count": 0}

    def _wrapped(agent_dict: dict) -> list[dict]:
        calls["count"] += 1
        return original(agent_dict)

    monkeypatch.setattr(context_builder_module, "_memory_v3_records", _wrapped)
    build_agent_context("bot1", agent, state, force_refresh=True)
    assert calls["count"] == 1
