from types import SimpleNamespace

from app.core.ticker import service as ticker_service


class _DummyDB:
    def close(self):
        return None


def test_runtime_key_present_skips_context_flag_reads(monkeypatch):
    monkeypatch.setattr(ticker_service, "_refresh_debug_context_cache", lambda db: {"ctx-1": "m-1"})
    monkeypatch.setattr("app.database.SessionLocal", lambda: _DummyDB())
    monkeypatch.setattr(ticker_service, "tick_match_many", lambda *args, **kwargs: {"ticks_advanced": 0})

    calls = {"flags": 0}

    def _fake_get_flag(*args, **kwargs):
        calls["flags"] += 1
        return False

    monkeypatch.setattr("app.core.state_cache.service.get_auto_tick_runtime", lambda ctx_id: {"enabled": True, "speed": "x100"})
    monkeypatch.setattr("app.core.state_cache.service.get_context_flag", _fake_get_flag)
    monkeypatch.setattr("app.core.state_cache.service.set_auto_tick_runtime", lambda *args, **kwargs: None)

    ticker_service.tick_debug_auto_matches()
    assert calls["flags"] == 0


def test_missing_runtime_key_falls_back_and_populates(monkeypatch):
    monkeypatch.setattr(ticker_service, "_refresh_debug_context_cache", lambda db: {"ctx-1": "m-1"})
    monkeypatch.setattr("app.database.SessionLocal", lambda: _DummyDB())
    monkeypatch.setattr(ticker_service, "tick_match_many", lambda *args, **kwargs: {"ticks_advanced": 0})

    set_calls = {"n": 0}

    monkeypatch.setattr("app.core.state_cache.service.get_auto_tick_runtime", lambda ctx_id: None)

    def _fake_get_flag(ctx_id, flag_name, default=None):
        if flag_name == "auto_tick_enabled":
            return True
        if flag_name == "auto_tick_speed":
            return "x100"
        return default

    monkeypatch.setattr("app.core.state_cache.service.get_context_flag", _fake_get_flag)
    monkeypatch.setattr(
        "app.core.state_cache.service.set_auto_tick_runtime",
        lambda *args, **kwargs: set_calls.__setitem__("n", set_calls["n"] + 1),
    )

    ticker_service.tick_debug_auto_matches()
    assert set_calls["n"] == 1


def test_disabled_auto_tick_resets_runtime_accumulator(monkeypatch):
    monkeypatch.setattr(ticker_service, "_refresh_debug_context_cache", lambda db: {"ctx-1": "m-1"})
    monkeypatch.setattr("app.database.SessionLocal", lambda: _DummyDB())
    monkeypatch.setattr("app.core.state_cache.service.get_auto_tick_runtime", lambda ctx_id: {"enabled": False, "speed": "x100"})
    monkeypatch.setattr("app.core.state_cache.service.get_context_flag", lambda *args, **kwargs: False)
    monkeypatch.setattr("app.core.state_cache.service.set_auto_tick_runtime", lambda *args, **kwargs: None)

    ticker_service._auto_tick_runtime["ctx-1"] = {"last_real_ts": 1.0, "game_seconds_accum": 120.0, "running": False}
    ticker_service.tick_debug_auto_matches()
    assert "ctx-1" not in ticker_service._auto_tick_runtime


def test_running_flag_cleared_after_tick_match_many_failure(monkeypatch):
    monkeypatch.setattr(ticker_service, "_refresh_debug_context_cache", lambda db: {"ctx-1": "m-1"})
    monkeypatch.setattr("app.database.SessionLocal", lambda: _DummyDB())
    monkeypatch.setattr("app.core.state_cache.service.get_auto_tick_runtime", lambda ctx_id: {"enabled": True, "speed": "x600"})
    monkeypatch.setattr("app.core.state_cache.service.get_context_flag", lambda *args, **kwargs: False)
    monkeypatch.setattr("app.core.state_cache.service.set_auto_tick_runtime", lambda *args, **kwargs: None)

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(ticker_service, "tick_match_many", _boom)
    ticker_service._auto_tick_runtime["ctx-1"] = {
        "last_real_ts": 0.0,
        "game_seconds_accum": 600.0,
        "running": False,
    }

    ticker_service.tick_debug_auto_matches()
    assert ticker_service._auto_tick_runtime["ctx-1"]["running"] is False


def test_max_catchup_batches_per_loop_one_calls_tick_once(monkeypatch):
    monkeypatch.setattr(ticker_service, "_refresh_debug_context_cache", lambda db: {"ctx-1": "m-1"})
    monkeypatch.setattr("app.database.SessionLocal", lambda: _DummyDB())
    monkeypatch.setattr("app.core.state_cache.service.get_auto_tick_runtime", lambda ctx_id: {"enabled": True, "speed": "x600"})
    monkeypatch.setattr("app.core.state_cache.service.get_context_flag", lambda *args, **kwargs: False)
    monkeypatch.setattr("app.core.state_cache.service.set_auto_tick_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        ticker_service,
        "_get_auto_tick_limits",
        lambda: {
            "max_ticks_per_batch": 30,
            "max_accumulated_ticks": 60,
            "max_ws_updates_per_second": 4.0,
            "max_catchup_batches_per_loop": 1,
            "catchup_mode": "accurate",
        },
    )
    calls = {"n": 0}

    def _fake_tick(*args, **kwargs):
        calls["n"] += 1
        return {"ticks_advanced": 30}

    monkeypatch.setattr(ticker_service, "tick_match_many", _fake_tick)
    ticker_service._auto_tick_runtime["ctx-1"] = {"last_real_ts": 0.0, "game_seconds_accum": 3600.0, "running": False}
    result = ticker_service.tick_debug_auto_matches()
    assert calls["n"] == 1
    assert result["ticked"] == 30


def test_max_catchup_batches_per_loop_two_calls_tick_twice_when_due(monkeypatch):
    monkeypatch.setattr(ticker_service, "_refresh_debug_context_cache", lambda db: {"ctx-1": "m-1"})
    monkeypatch.setattr("app.database.SessionLocal", lambda: _DummyDB())
    monkeypatch.setattr("app.core.state_cache.service.get_auto_tick_runtime", lambda ctx_id: {"enabled": True, "speed": "x600"})
    monkeypatch.setattr("app.core.state_cache.service.get_context_flag", lambda *args, **kwargs: False)
    monkeypatch.setattr("app.core.state_cache.service.set_auto_tick_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        ticker_service,
        "_get_auto_tick_limits",
        lambda: {
            "max_ticks_per_batch": 30,
            "max_accumulated_ticks": 120,
            "max_ws_updates_per_second": 4.0,
            "max_catchup_batches_per_loop": 2,
            "catchup_mode": "accurate",
        },
    )
    calls = {"n": 0}

    def _fake_tick(*args, **kwargs):
        calls["n"] += 1
        return {"ticks_advanced": 30}

    monkeypatch.setattr(ticker_service, "tick_match_many", _fake_tick)
    ticker_service._auto_tick_runtime["ctx-1"] = {"last_real_ts": 0.0, "game_seconds_accum": 5400.0, "running": False}
    result = ticker_service.tick_debug_auto_matches()
    assert calls["n"] == 2
    assert result["ticked"] == 60


def test_running_flag_cleared_if_second_catchup_batch_errors(monkeypatch):
    monkeypatch.setattr(ticker_service, "_refresh_debug_context_cache", lambda db: {"ctx-1": "m-1"})
    monkeypatch.setattr("app.database.SessionLocal", lambda: _DummyDB())
    monkeypatch.setattr("app.core.state_cache.service.get_auto_tick_runtime", lambda ctx_id: {"enabled": True, "speed": "x600"})
    monkeypatch.setattr("app.core.state_cache.service.get_context_flag", lambda *args, **kwargs: False)
    monkeypatch.setattr("app.core.state_cache.service.set_auto_tick_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        ticker_service,
        "_get_auto_tick_limits",
        lambda: {
            "max_ticks_per_batch": 30,
            "max_accumulated_ticks": 120,
            "max_ws_updates_per_second": 4.0,
            "max_catchup_batches_per_loop": 2,
            "catchup_mode": "accurate",
        },
    )
    calls = {"n": 0}

    def _fake_tick(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            return {"error": "boom"}
        return {"ticks_advanced": 30}

    monkeypatch.setattr(ticker_service, "tick_match_many", _fake_tick)
    ticker_service._auto_tick_runtime["ctx-1"] = {"last_real_ts": 0.0, "game_seconds_accum": 5400.0, "running": False}
    result = ticker_service.tick_debug_auto_matches()
    assert calls["n"] == 2
    assert result["ticked"] == 30
    assert ticker_service._auto_tick_runtime["ctx-1"]["running"] is False
