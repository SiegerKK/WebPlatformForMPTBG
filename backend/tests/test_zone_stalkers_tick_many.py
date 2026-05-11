from app.games.zone_stalkers.generators.zone_generator import generate_zone
from app.games.zone_stalkers.rules.tick_rules import _batch_stop_reason, tick_zone_map_many


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


def test_batch_stop_reason_stops_on_game_over():
    assert _batch_stop_reason({"game_over": True}, []) == "game_over"


def test_batch_stop_reason_stops_on_emission_warning():
    assert _batch_stop_reason({}, [{"event_type": "emission_warning"}]) == "emission_warning"


def test_batch_stop_reason_stops_on_emission_started():
    assert _batch_stop_reason({}, [{"event_type": "emission_started"}]) == "emission_started"


def test_batch_stop_reason_stops_on_emission_ended():
    assert _batch_stop_reason({}, [{"event_type": "emission_ended"}]) == "emission_ended"


def test_batch_stop_reason_stops_on_human_scheduled_action_completed():
    state = {"agents": {"player": {"controller": {"kind": "human"}}}}
    events = [{"event_type": "scheduled_action_completed", "payload": {"agent_id": "player"}}]
    assert _batch_stop_reason(state, events) == "human_action_completed"


def test_batch_stop_reason_does_not_stop_on_bot_scheduled_action_completed():
    state = {"agents": {"bot": {"controller": {"kind": "ai"}}}}
    events = [{"event_type": "scheduled_action_completed", "payload": {"agent_id": "bot"}}]
    assert _batch_stop_reason(state, events) is None


def test_batch_stop_reason_stops_on_human_combat_started():
    state = {
        "agents": {
            "player": {"controller": {"kind": "human"}},
            "bot": {"controller": {"kind": "ai"}},
        }
    }
    events = [{"event_type": "combat_started", "payload": {"attacker_id": "bot", "defender_id": "player"}}]
    assert _batch_stop_reason(state, events) == "human_combat_started"


def test_batch_stop_reason_does_not_stop_on_bot_only_combat_started():
    state = {
        "agents": {
            "bot_a": {"controller": {"kind": "ai"}},
            "bot_b": {"controller": {"kind": "ai"}},
        }
    }
    events = [{"event_type": "combat_started", "payload": {"attacker_id": "bot_a", "defender_id": "bot_b"}}]
    assert _batch_stop_reason(state, events) is None


def test_batch_stop_reason_stops_on_zone_event_choice_required():
    assert _batch_stop_reason({}, [{"event_type": "zone_event_choice_required"}]) == "zone_event_choice_required"


def test_batch_stop_reason_stops_on_requires_resync():
    assert _batch_stop_reason({}, [{"event_type": "requires_resync"}]) == "requires_resync"


def test_batch_stop_reason_respects_stop_on_decision():
    events = [{"event_type": "player_decision_required", "payload": {}}]
    assert _batch_stop_reason({}, events) is None
    assert _batch_stop_reason({}, events, stop_on_decision=True) == "player_decision_required"


def test_batch_stop_reason_respects_viewed_agent_id():
    state = {"agents": {"bot": {"controller": {"kind": "ai"}}}}
    events = [{"event_type": "combat_started", "payload": {"attacker_id": "bot", "defender_id": "other"}}]
    assert _batch_stop_reason(state, events) is None
    assert _batch_stop_reason(state, events, viewed_agent_id="bot") == "human_combat_started"
