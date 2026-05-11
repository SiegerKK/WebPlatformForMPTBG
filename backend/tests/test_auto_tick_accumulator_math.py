from app.core.ticker.service import _compute_due_ticks


def test_x600_one_second_gives_ten_ticks():
    due, rem, before = _compute_due_ticks(
        accumulated_game_seconds=0.0,
        elapsed_real_seconds=1.0,
        speed_multiplier=600,
        max_ticks_per_batch=30,
        max_accumulated_ticks=60,
    )
    assert due == 10
    assert before == 10
    assert rem == 0.0


def test_fractional_remainder_is_preserved():
    due, rem, before = _compute_due_ticks(
        accumulated_game_seconds=0.0,
        elapsed_real_seconds=0.25,
        speed_multiplier=600,
        max_ticks_per_batch=30,
        max_accumulated_ticks=60,
    )
    assert due == 2
    assert before == 2
    assert rem == 30.0  # 2.5 game minutes -> 2 ticks + 30 game seconds remainder


def test_due_ticks_capped_by_batch_limit():
    due, rem, before = _compute_due_ticks(
        accumulated_game_seconds=0.0,
        elapsed_real_seconds=10.0,
        speed_multiplier=600,
        max_ticks_per_batch=30,
        max_accumulated_ticks=60,
    )
    assert before >= 30
    assert due == 30
    assert rem >= 0.0


def test_accumulated_ticks_cap_is_enforced():
    due, rem, before = _compute_due_ticks(
        accumulated_game_seconds=0.0,
        elapsed_real_seconds=100.0,
        speed_multiplier=600,
        max_ticks_per_batch=30,
        max_accumulated_ticks=60,
    )
    # with cap=60 ticks, due_before_cap cannot exceed 60
    assert before == 60
    assert due == 30
    assert rem == 1800.0
