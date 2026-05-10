"""Safety regression tests for CPU PR1 follow-up fixes."""

from app.games.zone_stalkers.ruleset import _should_use_dirty_ws_delta
from app.games.zone_stalkers.runtime.tick_runtime import TickRuntime


def _make_world_state():
    from app.games.zone_stalkers.generators.zone_generator import generate_zone

    state = generate_zone(
        seed=42,
        num_players=1,
        num_ai_stalkers=0,
        num_mutants=0,
        num_traders=0,
    )
    state["player_agents"]["player1"] = "agent_p0"
    state["agents"]["agent_p0"]["controller"]["participant_id"] = "player1"
    return state


def test_dirty_delta_disabled_by_default_even_with_dirty_runtime():
    rt = TickRuntime()
    rt.dirty_agents.add("a1")
    state = {"cpu_dirty_delta_enabled": False}
    assert _should_use_dirty_ws_delta(state, rt) is False


def test_dirty_delta_enabled_when_flag_on_and_runtime_has_changes():
    rt = TickRuntime()
    rt.dirty_locations.add("loc_1")
    state = {"cpu_dirty_delta_enabled": True}
    assert _should_use_dirty_ws_delta(state, rt) is True


def test_tick_runtime_handoff_sets_last_and_clears_current():
    from app.games.zone_stalkers.rules.tick_rules import (
        _last_tick_runtime,
        _current_tick_runtime,
        tick_zone_map,
    )

    state = _make_world_state()
    _ = _last_tick_runtime
    _ = _current_tick_runtime
    tick_zone_map(state)

    from app.games.zone_stalkers.rules.tick_rules import (
        _last_tick_runtime as _last_after,
        _current_tick_runtime as _current_after,
    )
    assert _last_after is not None
    assert _current_after is None
    assert _last_after.profiler is not None
    prof = _last_after.profiler.to_dict()
    assert isinstance(prof.get("sections_ms"), dict)
    assert isinstance(prof.get("counters"), dict)
