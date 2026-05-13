from __future__ import annotations

from app.games.zone_stalkers.memory.cold_store import (
    clear_in_memory_store as clear_cold_store,
    clear_in_memory_store,
    ensure_agent_memory_loaded,
    get_agent_memory_ref,
    migrate_agent_memory_to_cold_store,
    reset_cold_metrics,
    save_agent_memory_if_dirty,
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


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.set_calls: list[tuple[str, bytes, int | None]] = []

    def get(self, key: str) -> bytes | None:
        return self.values.get(key)

    def set(self, key: str, value: bytes, ex: int | None = None) -> None:
        self.values[key] = value
        self.set_calls.append((key, value, ex))


def test_memory_write_uses_configured_redis_client() -> None:
    clear_cold_store()
    reset_cold_metrics()
    redis = FakeRedis()
    agent = make_agent(agent_id="bot1")
    ensure_memory_v3(agent)
    migrate_agent_memory_to_cold_store(
        context_id="ctx_mem",
        agent_id="bot1",
        agent=agent,
        redis_client=redis,
    )
    clear_cold_store()
    agent.pop("memory_v3", None)
    agent.pop("knowledge_v1", None)
    agent["memory_summary"]["is_loaded"] = False

    write_memory_event_to_v3(
        agent_id="bot1",
        agent=agent,
        legacy_entry={
            "world_turn": 120,
            "type": "action",
            "title": "rare artifact",
            "effects": {"action_kind": "rare_artifact_found", "location_id": "loc_a"},
        },
        world_turn=120,
        context_id="ctx_mem",
        cold_store_enabled=True,
        redis_client=redis,
    )
    assert isinstance(agent.get("memory_v3"), dict)
    assert redis.set_calls


def test_memory_write_missing_cold_key_does_not_create_partial_memory_blob() -> None:
    clear_cold_store()
    reset_cold_metrics()
    agent = make_agent(agent_id="bot1")
    agent.pop("memory_v3", None)
    agent.pop("knowledge_v1", None)
    agent["memory_ref"] = get_agent_memory_ref("ctx_missing", "bot1")
    agent["memory_summary"] = {"is_loaded": False, "dirty": False}

    write_memory_event_to_v3(
        agent_id="bot1",
        agent=agent,
        legacy_entry={
            "world_turn": 121,
            "type": "action",
            "title": "rare artifact",
            "effects": {"action_kind": "rare_artifact_found", "location_id": "loc_a"},
        },
        world_turn=121,
        context_id="ctx_missing",
        cold_store_enabled=True,
    )
    assert (agent.get("memory_summary") or {}).get("cold_load_error") == "missing_cold_memory_key"
    assert "memory_v3" not in agent
    assert (agent.get("memory_summary") or {}).get("dirty") is False


def test_memory_write_missing_cold_key_marks_error_and_does_not_dirty_save() -> None:
    clear_cold_store()
    reset_cold_metrics()
    agent = make_agent(agent_id="bot1")
    agent.pop("memory_v3", None)
    agent.pop("knowledge_v1", None)
    agent["memory_ref"] = get_agent_memory_ref("ctx_missing", "bot1")
    agent["memory_summary"] = {"is_loaded": False, "dirty": False}

    write_memory_event_to_v3(
        agent_id="bot1",
        agent=agent,
        legacy_entry={
            "world_turn": 122,
            "type": "observation",
            "title": "stalkers",
            "effects": {"action_kind": "stalkers_seen", "observed": "stalkers", "seen_agents": []},
        },
        world_turn=122,
        context_id="ctx_missing",
        cold_store_enabled=True,
    )
    assert (agent.get("memory_summary") or {}).get("cold_load_error") == "missing_cold_memory_key"
    assert (agent.get("memory_summary") or {}).get("dirty") is False
    assert save_agent_memory_if_dirty(context_id="ctx_missing", agent_id="bot1", agent=agent) is False


def test_critical_memory_write_missing_cold_key_does_not_overwrite_history() -> None:
    clear_cold_store()
    reset_cold_metrics()
    redis = FakeRedis()
    baseline_agent = make_agent(agent_id="bot1")
    ensure_memory_v3(baseline_agent)
    migrate_agent_memory_to_cold_store(
        context_id="ctx_critical",
        agent_id="bot1",
        agent=baseline_agent,
        redis_client=redis,
    )
    baseline_blob = redis.values[get_agent_memory_ref("ctx_critical", "bot1")]

    clear_cold_store()
    agent = make_agent(agent_id="bot1")
    agent.pop("memory_v3", None)
    agent.pop("knowledge_v1", None)
    agent["memory_ref"] = get_agent_memory_ref("ctx_missing_critical", "bot1")
    agent["memory_summary"] = {"is_loaded": False, "dirty": False}

    write_memory_event_to_v3(
        agent_id="bot1",
        agent=agent,
        legacy_entry={
            "world_turn": 123,
            "type": "action",
            "title": "critical",
            "effects": {"action_kind": "target_death_confirmed", "target_id": "enemy_1"},
        },
        world_turn=123,
        context_id="ctx_missing_critical",
        cold_store_enabled=True,
        redis_client=redis,
    )
    assert (agent.get("memory_summary") or {}).get("cold_load_error") == "missing_cold_memory_key"
    assert (agent.get("memory_summary") or {}).get("dirty") is False
    assert redis.values[get_agent_memory_ref("ctx_critical", "bot1")] == baseline_blob


def test_memory_events_cold_load_exception_records_error_and_does_not_write_partial_memory(monkeypatch) -> None:
    clear_cold_store()
    reset_cold_metrics()
    agent = make_agent(agent_id="bot1")
    agent.pop("memory_v3", None)
    agent.pop("knowledge_v1", None)
    agent["memory_ref"] = get_agent_memory_ref("ctx_exc", "bot1")
    agent["memory_summary"] = {"is_loaded": False, "dirty": False}

    import app.games.zone_stalkers.memory.cold_store as _cold_store_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("forced-load-error")

    monkeypatch.setattr(_cold_store_mod, "ensure_agent_memory_loaded", _raise)

    write_memory_event_to_v3(
        agent_id="bot1",
        agent=agent,
        legacy_entry={
            "world_turn": 124,
            "type": "action",
            "title": "critical",
            "effects": {"action_kind": "target_death_confirmed", "target_id": "enemy_1"},
        },
        world_turn=124,
        context_id="ctx_exc",
        cold_store_enabled=True,
    )
    summary = agent.get("memory_summary") or {}
    assert summary.get("cold_store_error") == "load_failed"
    assert summary.get("cold_store_error_type") == "RuntimeError"
    assert "memory_v3" not in agent
    assert summary.get("dirty") is False
