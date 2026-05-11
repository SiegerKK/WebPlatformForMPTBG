from types import SimpleNamespace

from app.core.matches.models import MatchStatus
from app.core.ticker import service as ticker_service


class _Q:
    def __init__(self, item):
        self.item = item

    def filter(self, *args, **kwargs):  # noqa: ARG002
        return self

    def first(self):
        return self.item


class _DB:
    def __init__(self, match_obj):
        self.match_obj = match_obj
        self.query_calls = 0

    def query(self, model):
        self.query_calls += 1
        # only Match query should be needed in this test path
        if model.__name__ == "Match":
            return _Q(self.match_obj)
        raise AssertionError(f"Unexpected query model: {model.__name__}")


def test_tick_match_uses_context_id_from_ruleset_without_zone_ctx_lookup(monkeypatch):
    fake_match = SimpleNamespace(id="m1", status=MatchStatus.ACTIVE, game_id="zone_stalkers")
    fake_db = _DB(fake_match)

    class _RuleSet:
        def tick(self, match_id, db):  # noqa: ARG002
            return {
                "context_id": "ctx-1",
                "new_events": [],
                "zone_delta": {"revision": 2},
                "world_turn": 10,
            }

    monkeypatch.setattr("app.core.commands.pipeline.get_ruleset", lambda game_id: _RuleSet())
    monkeypatch.setattr("app.core.ws.manager.ws_manager.notify", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.core.ws.manager.get_debug_subscriptions", lambda *args, **kwargs: {})
    monkeypatch.setattr("app.games.zone_stalkers.performance_metrics.record_tick_metrics", lambda *args, **kwargs: None)

    result = ticker_service.tick_match("m1", fake_db)
    assert "error" not in result
    # only one DB query (Match) should be needed due to context_id shortcut
    assert fake_db.query_calls == 1
