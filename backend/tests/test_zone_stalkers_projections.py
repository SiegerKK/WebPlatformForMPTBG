from __future__ import annotations

from app.games.zone_stalkers.performance_metrics import get_last_tick_metrics, get_tick_metrics, record_tick_metrics
from app.games.zone_stalkers.projections import build_zone_state_size_report, project_zone_state


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

