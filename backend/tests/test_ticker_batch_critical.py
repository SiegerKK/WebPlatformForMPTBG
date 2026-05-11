from app.core.ticker.service import _is_critical_batch_result


def test_is_critical_batch_result_by_game_over_state():
    result = {"new_state": {"game_over": True}, "new_events": []}
    assert _is_critical_batch_result(result) is True


def test_is_critical_batch_result_by_critical_event():
    result = {"new_state": {}, "new_events": [{"event_type": "emission_started"}]}
    assert _is_critical_batch_result(result) is True


def test_is_critical_batch_result_non_critical():
    result = {"new_state": {}, "new_events": [{"event_type": "bot_moved"}]}
    assert _is_critical_batch_result(result) is False
