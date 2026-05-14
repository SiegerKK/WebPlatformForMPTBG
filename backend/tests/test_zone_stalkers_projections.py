from __future__ import annotations

from app.games.zone_stalkers.memory.cold_store import (
    clear_in_memory_store,
    get_cold_metrics,
    get_agent_memory_ref,
    migrate_agent_memory_to_cold_store,
    reset_cold_metrics,
)
from app.games.zone_stalkers.performance_metrics import get_last_tick_metrics, get_tick_metrics, record_tick_metrics
from app.games.zone_stalkers.projections import build_zone_state_size_report, json_size_bytes, project_zone_state


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.set_calls: list[tuple[str, bytes, int | None]] = []

    def get(self, key: str) -> bytes | None:
        return self.values.get(key)

    def set(self, key: str, value: bytes, ex: int | None = None) -> None:
        self.values[key] = value
        self.set_calls.append((key, value, ex))


def _sample_state() -> dict:
    return {
        "context_type": "zone_map",
        "world_turn": 10,
        "agents": {
            "a1": {
                "id": "a1",
                "location_id": "loc_a",
                "memory": [{"kind": "observation"}],
                "memory_v3": {"records": {"r1": {"kind": "target_seen"}}},
                "brain_trace": {"events": [{"k": "v"}]},
                "active_plan_v3": {"status": "active"},
                "brain_v3_context": {
                    "objective_key": "TRACK_TARGET",
                    "hunt_target_belief": {
                        "target_id": "t1",
                        "best_location_id": "loc_b",
                        "best_location_confidence": 0.8,
                        "possible_locations": [{"location_id": "loc_b"} for _ in range(8)],
                        "likely_routes": [{"to_location_id": "loc_c"} for _ in range(8)],
                        "exhausted_locations": ["loc_x"],
                        "lead_count": 4,
                    },
                },
            }
        },
        "debug": {
            "hunt_search_by_agent": {f"h{i}": {"target_id": "t1"} for i in range(30)},
            "location_hunt_traces": {f"loc_{i}": {"negative_leads": []} for i in range(80)},
        },
        "traders": {
            "tr1": {
                "memory": [{"kind": "trader_note"}],
            }
        },
    }


def test_game_projection_strips_heavy_fields() -> None:
    state = _sample_state()
    projected = project_zone_state(state=state, mode="game")
    agent = projected["agents"]["a1"]
    assert "memory" not in agent
    assert "memory_v3" not in agent
    assert "brain_trace" not in agent
    assert "active_plan_v3" not in agent
    assert "brain_v3_context" not in agent
    assert "debug" not in projected


def test_debug_map_projection_keeps_bounded_hunt_data() -> None:
    state = _sample_state()
    projected = project_zone_state(state=state, mode="debug-map")
    agent = projected["agents"]["a1"]
    assert "memory" not in agent
    assert "memory_v3" not in agent
    assert agent["brain_v3_context"]["objective_key"] == "TRACK_TARGET"
    assert len(agent["brain_v3_context"]["hunt_target_belief"]["possible_locations"]) == 5
    assert len(projected["debug"]["hunt_search_by_agent"]) == 20
    assert len(projected["debug"]["location_hunt_traces"]) == 60


def test_state_size_report_has_projection_sizes() -> None:
    report = build_zone_state_size_report(_sample_state())
    assert report["state_size_bytes"] > 0
    assert report["full_projection_size_bytes"] >= report["game_projection_size_bytes"]
    assert report["debug_hunt_search_bytes"] > 0
    assert report["location_hunt_traces_bytes"] > 0


def test_tick_metrics_buffer_returns_latest() -> None:
    record_tick_metrics("match-proj-test", {"tick_total_ms": 12.5, "response_size_bytes": 128})
    latest = get_last_tick_metrics(match_id="match-proj-test")
    assert latest is not None
    assert latest["match_id"] == "match-proj-test"
    assert latest["tick_total_ms"] == 12.5
    assert get_tick_metrics(match_id="match-proj-test", limit=1)[0]["response_size_bytes"] == 128


def test_json_size_bytes_returns_positive_size() -> None:
    assert json_size_bytes({"key": "value", "n": 42}) > 0
    assert json_size_bytes([1, 2, 3]) > 0
    assert json_size_bytes({}) == len(b"{}")


def test_json_size_bytes_matches_game_projection_size() -> None:
    state = _sample_state()
    projected = project_zone_state(state=state, mode="game")
    direct_size = json_size_bytes(projected)
    report_size = build_zone_state_size_report(state)["game_projection_size_bytes"]
    assert direct_size == report_size


def test_game_projection_no_deepcopy_uses_explicit_builder():
    """game projection uses explicit builder — heavy fields excluded, summaries added."""
    state = _sample_state()
    state["agents"]["a1"]["equipment"] = {"weapon": "ak74", "armor": "stalker_suit", "artifact_slots": []}
    state["agents"]["a1"]["inventory"] = [{"id": "i1", "type": "medkit", "name": "Медкит", "weight": 0.5}]
    projected = project_zone_state(state=state, mode="game")
    agent = projected["agents"]["a1"]
    # Explicit summary fields present
    assert "active_plan_summary" in agent
    assert "equipment_summary" in agent
    assert "inventory_summary" in agent
    # Heavy fields absent
    assert "memory" not in agent
    assert "memory_v3" not in agent
    assert "brain_trace" not in agent
    assert "active_plan_v3" not in agent
    assert "brain_v3_context" not in agent
    # Debug absent at state level
    assert "debug" not in projected


def test_game_projection_has_world_state_fields():
    """game projection includes all world-level fields the frontend needs."""
    state = _sample_state()
    state["emission_active"] = True
    state["player_agents"] = {"user1": "a1"}
    state["auto_tick_enabled"] = True
    state["auto_tick_speed"] = "x100"
    projected = project_zone_state(state=state, mode="game")
    assert projected["world_turn"] == 10
    assert projected["emission_active"] is True
    assert projected["player_agents"] == {"user1": "a1"}
    assert projected["auto_tick_enabled"] is True
    assert projected["auto_tick_speed"] == "x100"


def test_game_projection_inventory_capped_at_20():
    """inventory_summary is capped at 20 items regardless of agent inventory size."""
    state = _sample_state()
    state["agents"]["a1"]["inventory"] = [
        {"id": f"i{n}", "type": "medkit", "name": f"Item {n}"}
        for n in range(50)
    ]
    projected = project_zone_state(state=state, mode="game")
    assert len(projected["agents"]["a1"]["inventory_summary"]) == 20


def test_game_projection_equipment_summary_compact():
    """equipment_summary contains only weapon/armor/artifact_slots."""
    state = _sample_state()
    state["agents"]["a1"]["equipment"] = {
        "weapon": "ak74",
        "armor": "seva_suit",
        "artifact_slots": ["moonlight"],
        "extra_field": "should_not_appear",
    }
    projected = project_zone_state(state=state, mode="game")
    eq = projected["agents"]["a1"]["equipment_summary"]
    assert eq["weapon"] == "ak74"
    assert eq["armor"] == "seva_suit"
    assert eq["artifact_slots"] == ["moonlight"]
    assert "extra_field" not in eq


def test_game_projection_active_plan_summary():
    """active_plan_summary is a compact subset of active_plan_v3."""
    state = _sample_state()
    state["agents"]["a1"]["active_plan_v3"] = {
        "status": "running",
        "plan_key": "hunt_plan",
        "current_step": 2,
        "objective_key": "TRACK_TARGET",
        "extra_large_field": [1] * 500,
    }
    projected = project_zone_state(state=state, mode="game")
    summary = projected["agents"]["a1"]["active_plan_summary"]
    assert summary["status"] == "running"
    assert summary["plan_key"] == "hunt_plan"
    assert summary["current_step"] == 2
    assert summary["objective_key"] == "TRACK_TARGET"
    assert "extra_large_field" not in summary


def test_game_projection_locations_include_required_fields():
    """Location projection includes connections, agents, artifacts, items."""
    state = _sample_state()
    state["locations"] = {
        "C1": {
            "id": "C1",
            "name": "КПП Кордон",
            "terrain_type": "military_buildings",
            "anomaly_activity": 2,
            "dominant_anomaly_type": None,
            "connections": [{"to": "C2", "type": "road", "travel_time": 30}],
            "agents": ["a1"],
            "artifacts": [],
            "items": [],
            "exit_zone": False,
            "region": "cordon",
        }
    }
    projected = project_zone_state(state=state, mode="game")
    loc = projected["locations"]["C1"]
    assert loc["name"] == "КПП Кордон"
    assert loc["connections"] == [{"to": "C2", "type": "road", "travel_time": 30}]
    assert loc["agents"] == ["a1"]
    assert loc["terrain_type"] == "military_buildings"



# ── debug-map-lite projection tests ──────────────────────────────────────────

def test_debug_map_lite_excludes_full_location_hunt_traces():
    state = _sample_state()
    projected = project_zone_state(state=state, mode="debug-map-lite")
    debug = projected.get("debug", {})
    # location_hunt_traces should be empty dict (stripped)
    assert debug.get("location_hunt_traces") == {}
    # but count summary should be present
    assert "location_hunt_traces_count" in debug
    assert debug["location_hunt_traces_count"] == 80


def test_debug_map_lite_excludes_full_hunt_search_by_agent():
    state = _sample_state()
    projected = project_zone_state(state=state, mode="debug-map-lite")
    debug = projected.get("debug", {})
    hsba = debug.get("hunt_search_by_agent", {})
    # Should be compact summaries — not the full data
    # All 30 agents still present
    assert len(hsba) == 30
    # But each entry should be compact (only summary fields)
    first_entry = next(iter(hsba.values()))
    assert "target_id" in first_entry
    assert "best_location_id" in first_entry
    assert "best_location_confidence" in first_entry
    assert "lead_count" in first_entry


def test_debug_map_lite_strips_agent_memory():
    state = _sample_state()
    projected = project_zone_state(state=state, mode="debug-map-lite")
    agent = projected["agents"]["a1"]
    assert "memory" not in agent
    assert "memory_v3" not in agent


def test_debug_map_lite_includes_revision_fields():
    state = _sample_state()
    state["state_revision"] = 42
    state["map_revision"] = 7
    projected = project_zone_state(state=state, mode="debug-map-lite")
    assert projected["state_revision"] == 42
    assert projected["map_revision"] == 7


def test_debug_map_lite_keeps_compact_brain_context():
    state = _sample_state()
    projected = project_zone_state(state=state, mode="debug-map-lite")
    agent = projected["agents"]["a1"]
    # brain_v3_context should be compacted (not full)
    ctx = agent.get("brain_v3_context")
    if ctx is not None:
        assert "objective_key" in ctx
        # possible_locations should be capped at 5
        hunt = ctx.get("hunt_target_belief")
        if hunt:
            assert len(hunt.get("possible_locations", [])) <= 5


def test_debug_map_projection_still_works():
    """Ensure the original debug-map projection still behaves as before."""
    state = _sample_state()
    projected = project_zone_state(state=state, mode="debug-map")
    assert len(projected["debug"]["hunt_search_by_agent"]) == 20
    assert len(projected["debug"]["location_hunt_traces"]) == 60


def test_size_report_includes_debug_map_lite():
    report = build_zone_state_size_report(_sample_state())
    assert "debug_map_lite_projection_size_bytes" in report
    assert report["debug_map_lite_projection_size_bytes"] > 0
    # lite should be smaller than full/raw state
    assert report["debug_map_lite_projection_size_bytes"] <= report["state_size_bytes"]


def test_full_projection_includes_memory_stats_story_and_terminal_state():
    state = _sample_state()
    state["agents"]["a1"]["has_left_zone"] = True
    state["agents"]["a1"]["global_goal_achieved"] = True
    state["agents"]["a1"]["current_goal"] = "restore_needs"
    state["agents"]["a1"]["scheduled_action"] = {"type": "explore_anomaly_location", "turns_remaining": 5}
    state["agents"]["a1"]["memory_v3"]["records"]["r2"] = {
        "id": "r2",
        "kind": "stalkers_seen",
        "layer": "episodic",
        "created_turn": 11,
        "summary": "saw stalkers",
        "details": {"action_kind": "stalkers_seen", "memory_type": "observation"},
    }
    projected = project_zone_state(state=state, mode="full")
    agent = projected["agents"]["a1"]
    assert "memory_v3_stats" in agent
    rgm = agent["memory_v3_stats"].get("runtime_global_metrics", {})
    assert "memory_write_attempts" in rgm
    assert "memory_write_written" in rgm
    assert "memory_write_aggregated" in rgm
    assert "memory_write_trace_only" in rgm
    assert "memory_by_tag_refs" in rgm
    assert "memory_by_tag_skipped_refs" in rgm
    assert "memory_health" in agent
    assert "story_events" in agent
    assert "sleep_need" in agent
    assert agent["current_goal"] == "left_zone"
    assert agent["scheduled_action"] is None
    assert agent["terminal_state"]["kind"] == "left_zone"
    first_story = agent["story_events"][0]
    assert {"world_turn", "type", "title", "summary", "source", "effects"} <= set(first_story.keys())


# ── PR4 projection tests: brain_context_cache / brain_context_metrics ─────────

def test_game_projection_strips_brain_context_cache() -> None:
    """PR4: brain_context_cache must not appear in game/zone-lite projections."""
    state = _sample_state()
    state["agents"]["a1"]["brain_context_cache"] = {
        "cache_key": {"location_id": "loc_a"},
        "derived": {"known_entities": []},
    }
    state["agents"]["a1"]["brain_context_metrics"] = {
        "context_builder_calls": 5,
        "context_builder_cache_hits": 3,
    }
    projected = project_zone_state(state=state, mode="game")
    agent = projected["agents"]["a1"]
    assert "brain_context_cache" not in agent
    assert "brain_context_metrics" not in agent


def test_debug_map_lite_strips_brain_context_cache_and_adds_metrics_summary() -> None:
    """PR4: debug-map-lite must strip brain_context_cache but include compact metrics."""
    state = _sample_state()
    state["agents"]["a1"]["brain_context_cache"] = {
        "cache_key": {"location_id": "loc_a"},
        "derived": {"known_entities": [{"agent_id": "x"}] * 50},
    }
    state["agents"]["a1"]["brain_context_metrics"] = {
        "context_builder_calls": 10,
        "context_builder_cache_hits": 7,
        "context_builder_cache_misses": 3,
        "context_builder_cache_hit_rate": 0.7,
        "context_builder_ms": 12.5,
    }
    projected = project_zone_state(state=state, mode="debug-map-lite")
    agent = projected["agents"]["a1"]
    assert "brain_context_cache" not in agent
    assert "brain_context_metrics" in agent
    m = agent["brain_context_metrics"]
    assert m["calls"] == 10
    assert m["hits"] == 7
    assert m["misses"] == 3
    assert m["hit_rate"] == 0.7


def test_debug_map_projection_strips_brain_context_cache() -> None:
    """PR4: debug-map deepcopy projection must strip brain_context_cache."""
    state = _sample_state()
    state["agents"]["a1"]["brain_context_cache"] = {
        "cache_key": {"location_id": "loc_a"},
        "derived": {"known_entities": []},
    }
    projected = project_zone_state(state=state, mode="debug-map")
    agent = projected["agents"]["a1"]
    assert "brain_context_cache" not in agent


def test_full_projection_exposes_brain_context_metrics_and_strips_cache() -> None:
    """PR4: full projection must include brain_context_metrics but strip brain_context_cache."""
    state = _sample_state()
    state["agents"]["a1"]["brain_context_cache"] = {
        "cache_key": {"location_id": "loc_a"},
        "derived": {"known_entities": []},
    }
    state["agents"]["a1"]["brain_context_metrics"] = {
        "context_builder_calls": 8,
        "context_builder_cache_hits": 5,
        "context_builder_cache_misses": 3,
        "context_builder_cache_hit_rate": 0.625,
        "context_builder_ms": 7.1,
    }
    projected = project_zone_state(state=state, mode="full")
    agent = projected["agents"]["a1"]
    assert "brain_context_cache" not in agent
    assert "brain_context_metrics" in agent
    m = agent["brain_context_metrics"]
    assert m["context_builder_calls"] == 8
    assert m["context_builder_cache_hits"] == 5


def test_full_projection_includes_compact_target_knowledge() -> None:
    state = _sample_state()
    state["world_turn"] = 120
    agent = state["agents"]["a1"]
    agent["kill_target_id"] = "t1"
    agent["knowledge_v1"] = {
        "revision": 3,
        "major_revision": 2,
        "minor_revision": 1,
        "known_npcs": {
            "t1": {
                "agent_id": "t1",
                "name": "Target",
                "last_seen_location_id": "loc_b",
                "last_seen_turn": 118,
                "last_direct_seen_turn": 118,
                "is_alive": True,
                "confidence": 0.9,
                "death_evidence": {"status": "alive"},
                "equipment_summary": {
                    "weapon_class": "rifle",
                    "armor_class": "medium",
                    "combat_strength_estimate": 0.7,
                },
            }
        },
        "known_corpses": {},
        "known_locations": {},
        "known_traders": {},
        "known_hazards": {},
        "hunt_evidence": {
            "t1": {
                "last_seen": {"location_id": "loc_b", "turn": 118, "confidence": 0.9, "source": "direct_observation"},
                "death": None,
                "route_hints": [{"to_location_id": "loc_c"}],
                "failed_search_locations": {"loc_x": {"count": 2}},
                "recent_contact": {"turn": 118, "location_id": "loc_b"},
                "revision": 4,
            }
        },
    }
    agent["brain_v3_context"]["_target_knowledge_debug"] = {
        "target_id": "t1",
        "legacy_memory_fallback_used": False,
        "lead_sources": {"visible": 1, "knowledge": 2, "memory_v3": 0, "debug_state": 0},
    }

    projected = project_zone_state(state=state, mode="full")
    target_knowledge = projected["agents"]["a1"]["target_knowledge"]
    assert target_knowledge["target_id"] == "t1"
    assert target_knowledge["known_npc"]["last_seen_location_id"] == "loc_b"
    assert target_knowledge["hunt_evidence"]["route_hints_count"] == 1
    assert target_knowledge["lead_sources"]["knowledge"] == 2


def test_debug_full_profile_loads_cold_memory() -> None:
    clear_in_memory_store()
    reset_cold_metrics()
    state = _sample_state()
    state["context_id"] = "ctx_proj_full"
    agent = state["agents"]["a1"]
    migrate_agent_memory_to_cold_store(context_id="ctx_proj_full", agent_id="a1", agent=agent)
    agent.pop("memory_v3", None)
    agent.pop("knowledge_v1", None)
    agent["memory_summary"]["is_loaded"] = False

    projected = project_zone_state(state=state, mode="full")
    assert "memory_v3" in projected["agents"]["a1"]
    assert int(get_cold_metrics()["cold_memory_loads"]) >= 1


def test_debug_full_profile_does_not_create_empty_blob_when_cold_key_missing() -> None:
    clear_in_memory_store()
    reset_cold_metrics()
    state = _sample_state()
    state["context_id"] = "ctx_proj_missing"
    agent = state["agents"]["a1"]
    agent.pop("memory_v3", None)
    agent.pop("knowledge_v1", None)
    agent["memory_ref"] = get_agent_memory_ref("ctx_proj_missing", "a1")
    agent["memory_summary"] = {"is_loaded": False, "dirty": False}

    projected = project_zone_state(state=state, mode="full")
    proj_agent = projected["agents"]["a1"]
    assert "memory_v3" not in proj_agent
    assert proj_agent.get("memory_summary", {}).get("cold_load_error") == "missing_cold_memory_key"


def test_game_projection_includes_memory_summary_but_not_memory_v3_or_knowledge_v1() -> None:
    state = _sample_state()
    state["agents"]["a1"]["memory_summary"] = {"records_count": 7, "dirty": False, "is_loaded": False}
    state["agents"]["a1"]["knowledge_v1"] = {"revision": 3}
    projected = project_zone_state(state=state, mode="game")
    agent = projected["agents"]["a1"]
    assert agent.get("memory_summary", {}).get("records_count") == 7
    assert "memory_v3" not in agent
    assert "knowledge_v1" not in agent


def test_full_projection_uses_configured_redis_client() -> None:
    clear_in_memory_store()
    reset_cold_metrics()
    redis = FakeRedis()
    state = _sample_state()
    state["context_id"] = "ctx_full_redis"
    agent = state["agents"]["a1"]
    migrate_agent_memory_to_cold_store(
        context_id="ctx_full_redis",
        agent_id="a1",
        agent=agent,
        redis_client=redis,
    )
    clear_in_memory_store()
    agent.pop("memory_v3", None)
    agent.pop("knowledge_v1", None)
    agent["memory_summary"]["is_loaded"] = False
    state["_zone_cold_memory_redis_client"] = redis

    projected = project_zone_state(state=state, mode="full")
    assert "memory_v3" in projected["agents"]["a1"]


def test_full_projection_includes_story_events_from_cold_memory() -> None:
    clear_in_memory_store()
    reset_cold_metrics()
    redis = FakeRedis()
    state = _sample_state()
    state["context_id"] = "ctx_story_cold"
    agent = state["agents"]["a1"]
    migrate_agent_memory_to_cold_store(
        context_id="ctx_story_cold",
        agent_id="a1",
        agent=agent,
        redis_client=redis,
    )
    clear_in_memory_store()
    agent.pop("memory_v3", None)
    agent.pop("knowledge_v1", None)
    agent["memory_summary"]["is_loaded"] = False
    state["_zone_cold_memory_redis_client"] = redis

    projected = project_zone_state(state=state, mode="full")
    assert isinstance(projected["agents"]["a1"].get("story_events"), list)
