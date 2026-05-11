from app.core.state_cache import service as cache_service


class _FakeRedis:
    def __init__(self):
        self.store = {}

    def set(self, key, value, ex=None):  # noqa: ARG002
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)


def test_set_and_get_auto_tick_runtime_roundtrip(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(cache_service, "get_redis", lambda: fake)

    cache_service.set_auto_tick_runtime(
        "ctx-1",
        enabled=True,
        speed="x600",
        updated_at=123.45,
    )
    payload = cache_service.get_auto_tick_runtime("ctx-1")

    assert payload is not None
    assert payload["enabled"] is True
    assert payload["speed"] == "x600"
    assert payload["updated_at"] == 123.45
