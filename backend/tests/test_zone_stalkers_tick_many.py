from app.games.zone_stalkers.generators.zone_generator import generate_zone
from app.games.zone_stalkers.rules.tick_rules import tick_zone_map_many


def test_tick_zone_map_many_advances_multiple_turns():
    state = generate_zone(seed=42, num_players=1)
    start_turn = int(state.get("world_turn", 0))

    new_state, events, ticks_advanced, stop_reason = tick_zone_map_many(state, 3)

    assert ticks_advanced >= 1
    assert int(new_state.get("world_turn", 0)) == start_turn + ticks_advanced
    assert isinstance(events, list)
    assert stop_reason in (None, "game_over")


def test_tick_zone_map_many_does_not_mutate_input():
    state = generate_zone(seed=42, num_players=1)
    old_turn = int(state.get("world_turn", 0))

    _new_state, _events, _ticks_advanced, _stop_reason = tick_zone_map_many(state, 2)

    assert int(state.get("world_turn", 0)) == old_turn
