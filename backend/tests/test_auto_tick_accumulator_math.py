from types import SimpleNamespace

from app.core.ticker.service import _compute_due_ticks, _get_auto_tick_limits


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


def test_smooth_catchup_mode_drops_excess_lag_remainder():
    due, rem, before = _compute_due_ticks(
        accumulated_game_seconds=0.0,
        elapsed_real_seconds=10.0,
        speed_multiplier=600,
        max_ticks_per_batch=30,
        max_accumulated_ticks=60,
        catchup_mode="smooth",
    )
    assert before == 60
    assert due == 30
    assert rem < 60.0


def test_compute_due_ticks_accurate_mode_preserves_capped_remainder():
    due, rem, before = _compute_due_ticks(
        accumulated_game_seconds=0.0,
        elapsed_real_seconds=10.0,
        speed_multiplier=600,
        max_ticks_per_batch=30,
        max_accumulated_ticks=60,
        catchup_mode="accurate",
    )
    assert due == 30
    assert before == 60
    assert rem == 1800.0


def test_get_auto_tick_limits_reads_settings(monkeypatch):
    monkeypatch.setattr(
        "app.config.settings",
        SimpleNamespace(
            AUTO_TICK_MAX_TICKS_PER_BATCH=15,
            AUTO_TICK_MAX_ACCUMULATED_TICKS=70,
            AUTO_TICK_MAX_WS_UPDATES_PER_SECOND=5.5,
            AUTO_TICK_MAX_CATCHUP_BATCHES_PER_LOOP=2,
            AUTO_TICK_CATCHUP_MODE="smooth",
        ),
    )
    limits = _get_auto_tick_limits()
    assert limits["max_ticks_per_batch"] == 15
    assert limits["max_accumulated_ticks"] == 70
    assert limits["max_ws_updates_per_second"] == 5.5
    assert limits["max_catchup_batches_per_loop"] == 2
    assert limits["catchup_mode"] == "smooth"


def test_get_auto_tick_limits_clamps_invalid_values(monkeypatch):
    monkeypatch.setattr(
        "app.config.settings",
        SimpleNamespace(
            AUTO_TICK_MAX_TICKS_PER_BATCH=0,
            AUTO_TICK_MAX_ACCUMULATED_TICKS=-5,
            AUTO_TICK_MAX_WS_UPDATES_PER_SECOND=0.0,
            AUTO_TICK_MAX_CATCHUP_BATCHES_PER_LOOP=0,
            AUTO_TICK_CATCHUP_MODE="accurate",
        ),
    )
    limits = _get_auto_tick_limits()
    assert limits["max_ticks_per_batch"] == 1
    assert limits["max_accumulated_ticks"] == 1
    assert limits["max_ws_updates_per_second"] == 0.1
    assert limits["max_catchup_batches_per_loop"] == 1
