from types import SimpleNamespace

from app.core.matches.models import MatchStatus
from app.core.ticker import service as ticker_service
from app.games.zone_stalkers.ruleset import ZoneStalkerRuleSet


class _Query:
    def __init__(self, db, model_name: str):
        self.db = db
        self.model_name = model_name

    def filter(self, *args, **kwargs):  # noqa: ARG002
        return self

    def first(self):
        if self.model_name == "Match":
            return self.db.match
        if self.model_name == "GameContext":
            self.db.zone_ctx_first_calls += 1
            return self.db.zone_ctx
        return None

    def all(self):
        if self.model_name == "GameContext":
            self.db.zone_event_all_calls += 1
            return self.db.event_ctxs
        return []


class _FakeDB:
    def __init__(self, *, match, zone_ctx, event_ctxs=None):
        self.match = match
        self.zone_ctx = zone_ctx
        self.event_ctxs = event_ctxs or []
        self.commits = 0
        self.rollbacks = 0
        self.zone_ctx_first_calls = 0
        self.zone_event_all_calls = 0

    def query(self, model):
        return _Query(self, getattr(model, "__name__", str(model)))

    def add(self, obj):  # noqa: ARG002
        return None

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _make_match_and_ctx():
    match = SimpleNamespace(id="m1", status=MatchStatus.ACTIVE, game_id="zone_stalkers", finished_at=None)
    zone_ctx = SimpleNamespace(id="ctx-1", match_id="m1", context_type="zone_map", status="active", state_blob={})
    return match, zone_ctx


def test_tick_many_loads_and_saves_state_once(monkeypatch):
    match, zone_ctx = _make_match_and_ctx()
    db = _FakeDB(match=match, zone_ctx=zone_ctx, event_ctxs=[])
    ruleset = ZoneStalkerRuleSet()
    counters = {"load": 0, "save": 0, "delta": 0}

    def _fake_load(*args, **kwargs):
        counters["load"] += 1
        return {"world_turn": 1, "active_events": [], "state_revision": 1, "_debug_revision": 1}

    def _fake_save(*args, **kwargs):
        counters["save"] += 1
        return False

    def _fake_tick_many(state, max_ticks):  # noqa: ARG001
        return (
            {"world_turn": 6, "active_events": [], "state_revision": 2, "_debug_revision": 2},
            [],
            5,
            None,
        )

    monkeypatch.setattr("app.core.state_cache.service.load_context_state", _fake_load)
    monkeypatch.setattr("app.core.state_cache.service.save_context_state", _fake_save)
    monkeypatch.setattr("app.games.zone_stalkers.rules.tick_rules.tick_zone_map_many", _fake_tick_many)
    monkeypatch.setattr("app.games.zone_stalkers.delta.build_zone_delta", lambda **kwargs: counters.__setitem__("delta", counters["delta"] + 1) or {"revision": 2})  # type: ignore[arg-type]

    result = ruleset.tick_many("m1", db, max_ticks=5)
    assert result["ticks_advanced"] == 5
    assert counters["load"] == 1
    assert counters["save"] == 1
    assert counters["delta"] == 1
    assert db.commits + db.rollbacks == 1


def test_tick_match_many_sends_one_ws_update_for_batch(monkeypatch):
    fake_match = SimpleNamespace(id="m1", status=MatchStatus.ACTIVE, game_id="zone_stalkers")
    db = _FakeDB(match=fake_match, zone_ctx=SimpleNamespace(id="ctx-1"))
    notify_calls = {"n": 0}

    class _RuleSet:
        def tick_many(self, match_id, db, max_ticks):  # noqa: ARG002
            return {
                "context_id": "ctx-1",
                "ticks_advanced": max_ticks,
                "new_events": [{"event_type": "bot_moved"}],
                "zone_delta": {"revision": 5},
                "new_state": {},
                "world_turn": 10,
            }

    monkeypatch.setattr("app.core.commands.pipeline.get_ruleset", lambda game_id: _RuleSet())
    monkeypatch.setattr("app.core.ws.manager.ws_manager.notify", lambda *args, **kwargs: notify_calls.__setitem__("n", notify_calls["n"] + 1))
    monkeypatch.setattr("app.games.zone_stalkers.performance_metrics.record_tick_metrics", lambda *args, **kwargs: None)
    monkeypatch.setattr(ticker_service, "_is_critical_batch_result", lambda result: True)

    ticker_service._last_ws_sent_ts.pop("m1", None)
    result = ticker_service.tick_match_many("m1", db, 5)
    assert "error" not in result
    assert notify_calls["n"] == 1


def test_tick_match_many_coalesces_non_critical_ws(monkeypatch):
    fake_match = SimpleNamespace(id="m1", status=MatchStatus.ACTIVE, game_id="zone_stalkers")
    db = _FakeDB(match=fake_match, zone_ctx=SimpleNamespace(id="ctx-1"))
    notify_calls = {"n": 0}

    class _RuleSet:
        def tick_many(self, match_id, db, max_ticks):  # noqa: ARG002
            return {
                "context_id": "ctx-1",
                "ticks_advanced": max_ticks,
                "new_events": [{"event_type": "bot_moved"}],
                "zone_delta": {"revision": 6},
                "new_state": {},
                "world_turn": 11,
            }

    monkeypatch.setattr("app.core.commands.pipeline.get_ruleset", lambda game_id: _RuleSet())
    monkeypatch.setattr("app.core.ws.manager.ws_manager.notify", lambda *args, **kwargs: notify_calls.__setitem__("n", notify_calls["n"] + 1))
    monkeypatch.setattr("app.games.zone_stalkers.performance_metrics.record_tick_metrics", lambda *args, **kwargs: None)
    monkeypatch.setattr(ticker_service, "_is_critical_batch_result", lambda result: False)
    monkeypatch.setattr(ticker_service, "_get_auto_tick_limits", lambda: {"max_ws_updates_per_second": 4.0})

    ticker_service._last_ws_sent_ts["m1"] = ticker_service.time.monotonic()
    result = ticker_service.tick_match_many("m1", db, 5)
    assert "error" not in result
    assert notify_calls["n"] == 0


def test_tick_match_many_bypasses_coalescing_for_critical(monkeypatch):
    fake_match = SimpleNamespace(id="m1", status=MatchStatus.ACTIVE, game_id="zone_stalkers")
    db = _FakeDB(match=fake_match, zone_ctx=SimpleNamespace(id="ctx-1"))
    notify_calls = {"n": 0}

    class _RuleSet:
        def tick_many(self, match_id, db, max_ticks):  # noqa: ARG002
            return {
                "context_id": "ctx-1",
                "ticks_advanced": max_ticks,
                "new_events": [{"event_type": "human_action_completed"}],
                "zone_delta": {"revision": 7},
                "stop_reason": "human_action_completed",
                "new_state": {},
                "world_turn": 12,
            }

    monkeypatch.setattr("app.core.commands.pipeline.get_ruleset", lambda game_id: _RuleSet())
    monkeypatch.setattr("app.core.ws.manager.ws_manager.notify", lambda *args, **kwargs: notify_calls.__setitem__("n", notify_calls["n"] + 1))
    monkeypatch.setattr("app.games.zone_stalkers.performance_metrics.record_tick_metrics", lambda *args, **kwargs: None)
    monkeypatch.setattr(ticker_service, "_is_critical_batch_result", lambda result: True)
    monkeypatch.setattr(ticker_service, "_get_auto_tick_limits", lambda: {"max_ws_updates_per_second": 4.0})

    ticker_service._last_ws_sent_ts["m1"] = ticker_service.time.monotonic()
    result = ticker_service.tick_match_many("m1", db, 5)
    assert "error" not in result
    assert notify_calls["n"] == 1


def test_single_tick_skips_active_zone_event_query_when_no_active_events(monkeypatch):
    match, zone_ctx = _make_match_and_ctx()
    db = _FakeDB(match=match, zone_ctx=zone_ctx, event_ctxs=[])
    ruleset = ZoneStalkerRuleSet()

    monkeypatch.setattr("app.core.state_cache.service.load_context_state", lambda *args, **kwargs: {"world_turn": 1, "active_events": [], "state_revision": 1, "_debug_revision": 1})
    monkeypatch.setattr("app.core.state_cache.service.save_context_state", lambda *args, **kwargs: True)
    monkeypatch.setattr("app.games.zone_stalkers.rules.tick_rules.tick_zone_map", lambda state: ({"world_turn": 2, "active_events": [], "state_revision": 2, "_debug_revision": 2}, []))
    monkeypatch.setattr("app.games.zone_stalkers.delta.build_zone_delta", lambda **kwargs: {"revision": 2})
    monkeypatch.setattr("app.games.zone_stalkers.performance_metrics.record_tick_metrics", lambda *args, **kwargs: None)

    result = ruleset.tick("m1", db)
    assert "error" not in result
    assert db.zone_event_all_calls == 0


def test_tick_many_allocates_event_sequence_once_for_batch(monkeypatch):
    match, zone_ctx = _make_match_and_ctx()
    db = _FakeDB(match=match, zone_ctx=zone_ctx, event_ctxs=[])
    ruleset = ZoneStalkerRuleSet()
    seq_calls = {"n": 0}

    monkeypatch.setattr("app.core.state_cache.service.load_context_state", lambda *args, **kwargs: {"world_turn": 1, "active_events": [], "state_revision": 1, "_debug_revision": 1})
    monkeypatch.setattr("app.core.state_cache.service.save_context_state", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        "app.games.zone_stalkers.rules.tick_rules.tick_zone_map_many",
        lambda state, max_ticks: (
            {"world_turn": 4, "active_events": [], "state_revision": 2, "_debug_revision": 2},
            [
                {"event_type": "bot_moved", "payload": {"agent_id": "bot_1"}},
                {"event_type": "bot_moved", "payload": {"agent_id": "bot_2"}},
                {"event_type": "bot_moved", "payload": {"agent_id": "bot_3"}},
            ],
            3,
            None,
        ),
    )
    monkeypatch.setattr(
        "app.core.events.service.allocate_sequence_numbers",
        lambda context_id, count, db_obj: seq_calls.__setitem__("n", seq_calls["n"] + 1) or list(range(1, count + 1)),  # type: ignore[arg-type]
    )
    monkeypatch.setattr("app.games.zone_stalkers.delta.build_zone_delta", lambda **kwargs: {"revision": 2})

    result = ruleset.tick_many("m1", db, max_ticks=3)
    assert "error" not in result
    assert result["ticks_advanced"] == 3
    assert seq_calls["n"] == 1
