from app.core.ticker.service import _build_batch_ws_payload, _is_critical_batch_result


def test_is_critical_batch_result_by_game_over_state():
    result = {"new_state": {"game_over": True}, "new_events": []}
    assert _is_critical_batch_result(result) is True


def test_critical_by_stop_reason_human_combat():
    result = {"new_state": {}, "new_events": [], "stop_reason": "human_combat_started"}
    assert _is_critical_batch_result(result) is True


def test_critical_by_stop_reason_zone_event_choice():
    result = {"new_state": {}, "new_events": [], "stop_reason": "zone_event_choice_required"}
    assert _is_critical_batch_result(result) is True


def test_critical_by_requires_resync():
    result = {"new_state": {}, "new_events": [], "requires_resync": True}
    assert _is_critical_batch_result(result) is True


def test_non_critical_bot_event_not_critical():
    result = {"new_state": {}, "new_events": [{"event_type": "bot_moved"}]}
    assert _is_critical_batch_result(result) is False


def test_non_critical_bot_combat_not_critical_if_no_stop_reason():
    result = {"new_state": {}, "new_events": [{"event_type": "combat_started"}]}
    assert _is_critical_batch_result(result) is False


def test_critical_by_human_action_completed_event():
    result = {"new_state": {}, "new_events": [{"event_type": "human_action_completed"}]}
    assert _is_critical_batch_result(result) is True


def test_batch_zone_delta_ws_payload_contains_batch_fields():
    result = {
        "ticks_advanced": 3,
        "world_turn": 100,
        "world_day": 1,
        "world_hour": 2,
        "world_minute": 3,
        "stop_reason": "emission_started",
    }
    payload = _build_batch_ws_payload(
        match_id="m1",
        context_id="c1",
        result=result,
        all_events=[],
        zone_delta={"revision": 7},
        requires_resync=False,
    )
    assert payload["type"] == "zone_delta"
    assert payload["ticks_advanced"] == 3
    assert payload["event_count"] == 0
    assert "new_events_preview" in payload
    assert payload["stop_reason"] == "emission_started"
