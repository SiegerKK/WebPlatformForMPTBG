from __future__ import annotations

import uuid

import app.games.zone_stalkers.decision.context_builder as context_builder_module
from app.games.zone_stalkers.decision.context_builder import (
    CONTEXT_CACHE_TURN_BUCKET_SIZE,
    build_agent_context,
)
from app.games.zone_stalkers.memory.cold_store import (
    clear_in_memory_store,
    flush_dirty_agent_memories,
    get_cold_metrics,
    migrate_agent_memory_to_cold_store,
    reset_cold_metrics,
)
from app.games.zone_stalkers.knowledge.knowledge_store import upsert_known_npc
from app.games.zone_stalkers.memory.store import ensure_memory_v3
from app.games.zone_stalkers.rules.tick_constants import CRITICAL_THIRST_THRESHOLD
from tests.decision.conftest import make_agent, make_minimal_state

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _misses(agent: dict) -> int:
    return agent["brain_context_metrics"]["context_builder_cache_misses"]


def _hits(agent: dict) -> int:
    return agent["brain_context_metrics"]["context_builder_cache_hits"]


def _add_target_seen_record(agent: dict, target_id: str, location_id: str, turn: int = 99) -> None:
    mem = ensure_memory_v3(agent)
    rec_id = str(uuid.uuid4())
    mem["records"][rec_id] = {
        "id": rec_id,
        "kind": "target_seen",
        "title": "saw target",
        "summary": "target seen",
        "created_turn": turn,
        "status": "active",
        "location_id": location_id,
        "entity_ids": [target_id],
        "details": {"target_id": target_id, "location_id": location_id},
        "tags": ["target"],
    }
    mem["stats"]["memory_revision"] = int(mem["stats"].get("memory_revision", 0)) + 1


# ─────────────────────────────────────────────────────────────────────────────
# PR4 cache — basic hit / miss
# ─────────────────────────────────────────────────────────────────────────────


def test_context_builder_cache_hit_for_same_revision_location_objective() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)

    build_agent_context("bot1", agent, state)
    assert _misses(agent) == 1
    assert _hits(agent) == 0

    # Turn advances but stays in the same bucket (100 → 101 are both in bucket 10)
    state["world_turn"] = 101
    build_agent_context("bot1", agent, state)
    assert _hits(agent) == 1
    assert _misses(agent) == 1


def test_context_builder_cache_hit_within_same_turn_bucket() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)
    # Use explicit turn at start of a bucket
    state["world_turn"] = 100  # bucket 10
    build_agent_context("bot1", agent, state)
    assert _misses(agent) == 1

    state["world_turn"] = 109  # still bucket 10
    build_agent_context("bot1", agent, state)
    assert _hits(agent) == 1
    assert _misses(agent) == 1


# ─────────────────────────────────────────────────────────────────────────────
# PR4 cache — invalidation on key change
# ─────────────────────────────────────────────────────────────────────────────


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

    assert _misses(agent) == 2


def test_context_builder_cache_invalidates_on_location_change() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)

    build_agent_context("bot1", agent, state)
    agent["location_id"] = "loc_b"
    build_agent_context("bot1", agent, state)

    assert _misses(agent) == 2


def test_context_builder_cache_invalidates_on_objective_change() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)

    build_agent_context("bot1", agent, state)
    agent["active_objective"] = {"key": "KILL_STALKER"}
    build_agent_context("bot1", agent, state)

    assert _misses(agent) == 2


def test_context_builder_cache_invalidates_on_target_change() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)

    build_agent_context("bot1", agent, state)
    agent["kill_target_id"] = "target_1"
    build_agent_context("bot1", agent, state)

    assert _misses(agent) == 2


def test_context_builder_cache_invalidates_on_global_goal_change() -> None:
    agent = make_agent(global_goal="get_rich")
    state = make_minimal_state(agent=agent)

    build_agent_context("bot1", agent, state)
    agent["global_goal"] = "kill_stalker"
    build_agent_context("bot1", agent, state)

    assert _misses(agent) == 2


def test_context_builder_cache_invalidates_on_emission_phase_change() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)
    state["emission_active"] = False
    state.pop("emission_scheduled_turn", None)

    build_agent_context("bot1", agent, state)
    state["emission_active"] = True
    build_agent_context("bot1", agent, state)

    assert _misses(agent) == 2


def test_context_builder_cache_invalidates_on_world_turn_bucket_change() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)
    state["world_turn"] = 100  # bucket 10

    build_agent_context("bot1", agent, state)
    assert _misses(agent) == 1

    state["world_turn"] = 100 + CONTEXT_CACHE_TURN_BUCKET_SIZE  # bucket 11
    build_agent_context("bot1", agent, state)
    assert _misses(agent) == 2


def test_context_builder_cache_invalidates_on_memory_revision_change() -> None:
    """PR2 regression: memory_revision in cache key must invalidate on write."""
    agent = make_agent()
    state = make_minimal_state(agent=agent)

    build_agent_context("bot1", agent, state)
    assert _misses(agent) == 1

    mem_v3 = ensure_memory_v3(agent)
    mem_v3["stats"]["memory_revision"] = int(mem_v3["stats"].get("memory_revision", 0)) + 1
    build_agent_context("bot1", agent, state)
    assert _misses(agent) == 2


# ─────────────────────────────────────────────────────────────────────────────
# PR4 cache — bypass conditions
# ─────────────────────────────────────────────────────────────────────────────


def test_context_builder_cache_bypassed_for_combat_active() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)

    build_agent_context("bot1", agent, state)
    state["combat_interactions"]["bot1"] = {"enemy_id": "target_1"}
    build_agent_context("bot1", agent, state)

    assert _hits(agent) == 0
    assert _misses(agent) == 2


def test_context_builder_cache_bypassed_for_critical_survival_need() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)

    build_agent_context("bot1", agent, state)
    agent["thirst"] = CRITICAL_THIRST_THRESHOLD
    build_agent_context("bot1", agent, state)

    assert _hits(agent) == 0
    assert _misses(agent) == 2


def test_context_builder_cache_bypassed_for_dead_agent() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)

    build_agent_context("bot1", agent, state)
    agent["is_alive"] = False
    build_agent_context("bot1", agent, state)

    assert _hits(agent) == 0
    assert _misses(agent) == 2


def test_context_builder_cache_bypassed_for_left_zone_agent() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)

    build_agent_context("bot1", agent, state)
    agent["has_left_zone"] = True
    build_agent_context("bot1", agent, state)

    assert _hits(agent) == 0
    assert _misses(agent) == 2


def test_context_builder_cache_bypassed_for_force_refresh() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)

    build_agent_context("bot1", agent, state)
    build_agent_context("bot1", agent, state, force_refresh=True)

    assert _hits(agent) == 0
    assert _misses(agent) == 2


def test_context_builder_cache_bypassed_for_deep_debug() -> None:
    agent = make_agent()
    state = make_minimal_state(agent=agent)

    build_agent_context("bot1", agent, state)
    build_agent_context("bot1", agent, state, deep_debug=True)

    assert _hits(agent) == 0
    assert _misses(agent) == 2


def test_context_builder_cache_bypassed_for_state_debug_flag() -> None:
    """bypass when state['debug']['deep_context_builder'] is True."""
    agent = make_agent()
    state = make_minimal_state(agent=agent)

    build_agent_context("bot1", agent, state)
    state["debug"] = {"deep_context_builder": True}
    build_agent_context("bot1", agent, state)

    assert _hits(agent) == 0
    assert _misses(agent) == 2


def test_context_builder_cache_not_bypassed_when_debug_is_not_dict() -> None:
    """Non-dict state['debug'] must not crash or bypass the cache."""
    agent = make_agent()
    state = make_minimal_state(agent=agent)

    build_agent_context("bot1", agent, state)
    state["debug"] = "some_string_not_a_dict"
    build_agent_context("bot1", agent, state)

    assert _hits(agent) == 1  # no bypass happened


# ─────────────────────────────────────────────────────────────────────────────
# PR4 cache — mutable safety
# ─────────────────────────────────────────────────────────────────────────────


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


def test_cached_nested_dicts_are_not_shared() -> None:
    """Nested dict mutation in a cached ctx must not pollute subsequent builds."""
    agent = make_agent()
    state = make_minimal_state(agent=agent)
    state["agents"]["other_1"] = make_agent(agent_id="other_1", location_id="loc_b")
    upsert_known_npc(
        agent,
        other_agent_id="other_1",
        name="Original",
        location_id="loc_b",
        world_turn=state["world_turn"],
        source="direct_observation",
        confidence=0.9,
    )

    first_ctx = build_agent_context("bot1", agent, state)
    assert any(e["agent_id"] == "other_1" for e in first_ctx.known_entities)

    # Mutate a nested dict on the returned context
    for entity in first_ctx.known_entities:
        if entity.get("agent_id") == "other_1":
            entity["name"] = "MUTATED"
            break

    second_ctx = build_agent_context("bot1", agent, state)
    for entity in second_ctx.known_entities:
        if entity.get("agent_id") == "other_1":
            assert entity["name"] != "MUTATED"
            break


# ─────────────────────────────────────────────────────────────────────────────
# PR2 regression — memory scan counts
# ─────────────────────────────────────────────────────────────────────────────


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


def test_cache_hit_does_not_scan_memory(monkeypatch) -> None:
    """PR2 regression: on a cache hit, memory_v3_records must NOT be called."""
    agent = make_agent()
    state = make_minimal_state(agent=agent)

    original = context_builder_module._memory_v3_records
    calls = {"count": 0}

    def _wrapped(agent_dict: dict) -> list[dict]:
        calls["count"] += 1
        return original(agent_dict)

    monkeypatch.setattr(context_builder_module, "_memory_v3_records", _wrapped)

    build_agent_context("bot1", agent, state)  # miss — populates cache
    scan_after_miss = calls["count"]

    build_agent_context("bot1", agent, state)  # hit — must not scan memory
    scan_after_hit = calls["count"]

    assert _hits(agent) == 1
    assert scan_after_hit == scan_after_miss  # no additional scan on hit


# ─────────────────────────────────────────────────────────────────────────────
# PR1 regression — no memory writes from context builder
# ─────────────────────────────────────────────────────────────────────────────


def test_context_cache_does_not_write_memory_records() -> None:
    """PR1 regression: context builder must not create memory_v3 records."""
    agent = make_agent()
    state = make_minimal_state(agent=agent)

    mem_before = ensure_memory_v3(agent)
    attempts_before = int(mem_before["stats"].get("memory_write_attempts", 0))
    count_before = len(mem_before["records"])

    build_agent_context("bot1", agent, state)
    build_agent_context("bot1", agent, state)

    mem_after = ensure_memory_v3(agent)
    assert len(mem_after["records"]) == count_before
    assert int(mem_after["stats"].get("memory_write_attempts", 0)) == attempts_before


# ─────────────────────────────────────────────────────────────────────────────
# PR3 regression — knowledge_v1 takes priority over memory_v3
# ─────────────────────────────────────────────────────────────────────────────


def test_context_builder_cache_uses_knowledge_before_memory_fallback() -> None:
    """PR3 regression: knowledge_v1 location must win over conflicting memory_v3 entry."""
    agent = make_agent()
    state = make_minimal_state(agent=agent)
    state["agents"]["other_1"] = make_agent(agent_id="other_1", location_id="loc_a")

    # Knowledge says other_1 is at loc_b
    upsert_known_npc(
        agent,
        other_agent_id="other_1",
        name="Other",
        location_id="loc_b",
        world_turn=state["world_turn"],
        source="direct_observation",
        confidence=0.95,
    )

    # Memory says other_1 was at loc_a in a conflicting record.
    # The context builder must prefer knowledge_v1 over this memory entry.
    _add_target_seen_record(agent, "other_1", "loc_a", turn=50)

    ctx = build_agent_context("bot1", agent, state)
    entity = next((e for e in ctx.known_entities if e.get("agent_id") == "other_1"), None)
    assert entity is not None
    # The first match must come from knowledge (loc_b), not memory (loc_a).
    # Knowledge builder uses "last_known_location" for the entity dict.
    assert entity.get("last_known_location") == "loc_b"


def test_context_builder_cache_invalidates_when_known_npc_death_status_changes() -> None:
    """PR3 regression: knowledge_revision bump from death_status triggers cache miss."""
    agent = make_agent()
    state = make_minimal_state(agent=agent)
    state["agents"]["target_x"] = make_agent(agent_id="target_x", location_id="loc_b")

    upsert_known_npc(
        agent,
        other_agent_id="target_x",
        name="TargetX",
        location_id="loc_b",
        world_turn=50,
        source="direct_observation",
        confidence=0.8,
    )
    ctx1 = build_agent_context("bot1", agent, state)
    misses_1 = _misses(agent)

    target_entity = next((e for e in ctx1.known_entities if e.get("agent_id") == "target_x"), None)
    assert target_entity is not None

    # Report target as dead — this updates knowledge_revision
    upsert_known_npc(
        agent,
        other_agent_id="target_x",
        name="TargetX",
        location_id="loc_b",
        world_turn=100,
        source="corpse_seen",
        confidence=0.99,
        death_status={"is_alive": False, "death_turn": 100, "corpse_location_id": "loc_b"},
    )

    ctx2 = build_agent_context("bot1", agent, state)
    assert _misses(agent) == misses_1 + 1

    # Verify the alive status reflects the death update
    dead_entity = next((e for e in ctx2.known_entities if e.get("agent_id") == "target_x"), None)
    assert dead_entity is not None
    assert dead_entity.get("is_alive") is False


# ─────────────────────────────────────────────────────────────────────────────
# PR4 — target_leads / corpse_leads populated in cache
# ─────────────────────────────────────────────────────────────────────────────


def test_target_leads_populated_from_knowledge_and_memory() -> None:
    """target_leads should reflect knowledge_v1 and target_seen memory records."""
    agent = make_agent()
    state = make_minimal_state(agent=agent)
    state["agents"]["target_1"] = make_agent(agent_id="target_1", location_id="loc_b")
    agent["kill_target_id"] = "target_1"

    # Add knowledge entry for target
    upsert_known_npc(
        agent,
        other_agent_id="target_1",
        name="Target",
        location_id="loc_b",
        world_turn=90,
        source="direct_observation",
        confidence=0.8,
    )

    # Add memory record for target_seen
    _add_target_seen_record(agent, "target_1", "loc_b", turn=95)

    # Normal build (no force_refresh) so cache is stored
    build_agent_context("bot1", agent, state)
    cache = agent.get("brain_context_cache", {})
    derived = cache.get("derived", {})
    target_leads = derived.get("target_leads", [])

    assert len(target_leads) >= 1
    knowledge_lead = next((l for l in target_leads if l.get("source") == "knowledge_v1"), None)
    assert knowledge_lead is not None


def test_corpse_leads_populated_from_memory() -> None:
    """corpse_leads should capture corpse_seen records for the target."""
    agent = make_agent()
    state = make_minimal_state(agent=agent)
    agent["kill_target_id"] = "target_1"

    mem = ensure_memory_v3(agent)
    rec_id = str(uuid.uuid4())
    mem["records"][rec_id] = {
        "id": rec_id,
        "kind": "corpse_seen",
        "title": "saw corpse",
        "summary": "corpse at loc_b",
        "created_turn": 99,
        "status": "active",
        "location_id": "loc_b",
        "entity_ids": ["target_1"],
        "details": {"dead_agent_id": "target_1", "corpse_location_id": "loc_b"},
        "tags": ["corpse"],
    }
    mem["stats"]["memory_revision"] = 1

    # Normal build (no force_refresh) so cache is stored
    build_agent_context("bot1", agent, state)
    cache = agent.get("brain_context_cache", {})
    derived = cache.get("derived", {})
    corpse_leads = derived.get("corpse_leads", [])

    assert len(corpse_leads) >= 1
    assert corpse_leads[0]["kind"] == "corpse_seen"


def test_target_leads_invalidate_on_memory_revision_change() -> None:
    """target_leads in cache must refresh when memory_revision changes."""
    agent = make_agent()
    state = make_minimal_state(agent=agent)
    agent["kill_target_id"] = "target_1"
    state["agents"]["target_1"] = make_agent(agent_id="target_1", location_id="loc_b")

    build_agent_context("bot1", agent, state)
    misses_before = _misses(agent)

    _add_target_seen_record(agent, "target_1", "loc_b", turn=200)
    build_agent_context("bot1", agent, state)

    assert _misses(agent) == misses_before + 1


def test_context_builder_cache_hit_does_not_load_cold_memory() -> None:
    clear_in_memory_store()
    reset_cold_metrics()
    agent = make_agent()
    state = make_minimal_state(agent=agent)
    state["context_id"] = "ctx_cache_hit"

    build_agent_context("bot1", agent, state)  # warm cache
    reset_cold_metrics()
    build_agent_context("bot1", agent, state)  # cache hit
    metrics = get_cold_metrics()
    assert int(metrics["cold_memory_loads"]) == 0


def test_context_builder_cache_miss_loads_cold_memory_for_knowledge() -> None:
    clear_in_memory_store()
    reset_cold_metrics()
    agent = make_agent()
    state = make_minimal_state(agent=agent)
    state["context_id"] = "ctx_cache_miss"
    migrate_agent_memory_to_cold_store(context_id="ctx_cache_miss", agent_id="bot1", agent=agent)
    # strip hot memory/knowledge to force load from cold on miss
    flush_dirty_agent_memories(context_id="ctx_cache_miss", state={"agents": {"bot1": agent}})

    build_agent_context("bot1", agent, state)
    metrics = get_cold_metrics()
    assert int(metrics["cold_memory_loads"]) >= 1


def test_context_builder_force_refresh_loads_cold_memory() -> None:
    clear_in_memory_store()
    reset_cold_metrics()
    agent = make_agent()
    state = make_minimal_state(agent=agent)
    state["context_id"] = "ctx_force_refresh"
    migrate_agent_memory_to_cold_store(context_id="ctx_force_refresh", agent_id="bot1", agent=agent)
    build_agent_context("bot1", agent, state)  # warm cache while loaded
    flush_dirty_agent_memories(context_id="ctx_force_refresh", state={"agents": {"bot1": agent}})
    reset_cold_metrics()

    build_agent_context("bot1", agent, state, force_refresh=True)
    metrics = get_cold_metrics()
    assert int(metrics["cold_memory_loads"]) >= 1


def test_context_builder_after_strip_can_rebuild_from_cold_memory() -> None:
    clear_in_memory_store()
    reset_cold_metrics()
    agent = make_agent()
    state = make_minimal_state(agent=agent)
    state["context_id"] = "ctx_strip_rebuild"
    migrate_agent_memory_to_cold_store(context_id="ctx_strip_rebuild", agent_id="bot1", agent=agent)
    flush_dirty_agent_memories(context_id="ctx_strip_rebuild", state={"agents": {"bot1": agent}})

    ctx = build_agent_context("bot1", agent, state)
    assert ctx is not None
    assert isinstance(agent.get("knowledge_v1"), dict)
