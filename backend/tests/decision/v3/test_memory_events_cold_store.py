from __future__ import annotations

from app.games.zone_stalkers.memory.cold_store import (
    clear_in_memory_store,
    ensure_agent_memory_loaded,
    migrate_agent_memory_to_cold_store,
    reset_cold_metrics,
)
from app.games.zone_stalkers.memory.memory_events import write_memory_event_to_v3
from app.games.zone_stalkers.memory.store import ensure_memory_v3
from tests.decision.conftest import make_agent


def _prepare_migrated_agent() -> dict:
    agent = make_agent(agent_id="bot1")
    ensure_memory_v3(agent)
    migrate_agent_memory_to_cold_store(context_id="ctx_mem", agent_id="bot1", agent=agent)
    agent.pop("memory_v3", None)
    agent.pop("knowledge_v1", None)
    agent["memory_summary"]["is_loaded"] = False
    return agent


def test_memory_write_loads_cold_memory_and_marks_dirty() -> None:
    clear_in_memory_store()
    reset_cold_metrics()
    agent = _prepare_migrated_agent()

    write_memory_event_to_v3(
        agent_id="bot1",
        agent=agent,
        legacy_entry={
            "world_turn": 100,
            "type": "action",
            "title": "kill confirmed",
            "effects": {"action_kind": "target_death_confirmed", "target_id": "target_1"},
        },
        world_turn=100,
        context_id="ctx_mem",
        cold_store_enabled=True,
    )

    summary = agent.get("memory_summary") or {}
    assert summary.get("is_loaded") is True
    assert summary.get("dirty") is True
    assert isinstance(agent.get("memory_v3"), dict)


def test_memory_summary_updates_after_memory_write() -> None:
    clear_in_memory_store()
    reset_cold_metrics()
    agent = _prepare_migrated_agent()
    ensure_agent_memory_loaded(context_id="ctx_mem", agent_id="bot1", agent=agent)
    before = int((agent.get("memory_summary") or {}).get("records_count", 0))

    write_memory_event_to_v3(
        agent_id="bot1",
        agent=agent,
        legacy_entry={
            "world_turn": 101,
            "type": "action",
            "title": "rare artifact",
            "effects": {"action_kind": "rare_artifact_found", "location_id": "loc_a"},
        },
        world_turn=101,
        context_id="ctx_mem",
        cold_store_enabled=True,
    )

    after = int((agent.get("memory_summary") or {}).get("records_count", 0))
    assert after >= before + 1


def test_trace_only_event_does_not_load_cold_memory_or_mark_dirty() -> None:
    clear_in_memory_store()
    reset_cold_metrics()
    agent = _prepare_migrated_agent()

    write_memory_event_to_v3(
        agent_id="bot1",
        agent=agent,
        legacy_entry={
            "world_turn": 102,
            "type": "action",
            "title": "trace only",
            "effects": {"action_kind": "active_plan_created"},
        },
        world_turn=102,
        context_id="ctx_mem",
        cold_store_enabled=True,
    )

    summary = agent.get("memory_summary") or {}
    assert summary.get("is_loaded") is False
    assert summary.get("dirty") is False
    assert "memory_v3" not in agent


def test_knowledge_upsert_marks_cold_memory_dirty() -> None:
    clear_in_memory_store()
    reset_cold_metrics()
    agent = _prepare_migrated_agent()

    write_memory_event_to_v3(
        agent_id="bot1",
        agent=agent,
        legacy_entry={
            "world_turn": 103,
            "type": "observation",
            "title": "seen stalker",
            "effects": {
                "action_kind": "stalkers_seen",
                "observed": "stalkers",
                "seen_agents": [{"agent_id": "enemy_1", "location_id": "loc_b", "name": "Enemy"}],
            },
        },
        world_turn=103,
        context_id="ctx_mem",
        cold_store_enabled=True,
    )

    summary = agent.get("memory_summary") or {}
    assert summary.get("dirty") is True
