"""
Unit tests for the framework-level state cache (app.core.state_cache).

These tests mock ``get_redis()`` so they work without a real Redis server —
the same way CI and the existing test suite run.
"""
from __future__ import annotations

import json
import zlib
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_ctx(state: dict) -> MagicMock:
    """Create a minimal fake ORM context object."""
    ctx = MagicMock()
    ctx.id = "ctx-uuid-001"
    ctx.state_blob = state
    ctx.state_version = 0
    return ctx


def _compress(state: dict) -> bytes:
    return zlib.compress(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        level=6,
    )


# ── Tests: load_context_state ─────────────────────────────────────────────────


class TestLoadContextState:
    def test_redis_hit_returns_cached_state(self):
        """When Redis has the key, load_context_state returns the Redis value."""
        from app.core.state_cache.service import load_context_state

        cached = {"world_turn": 42, "agents": {}}
        r = MagicMock()
        r.get.return_value = _compress(cached)

        with patch("app.core.state_cache.service.get_redis", return_value=r):
            ctx = _make_ctx({"world_turn": 1})
            result = load_context_state("ctx-1", ctx)

        assert result == cached
        r.get.assert_called_once()

    def test_redis_miss_falls_back_to_db(self):
        """When Redis key is absent, the context's state_blob is returned."""
        from app.core.state_cache.service import load_context_state

        r = MagicMock()
        r.get.return_value = None  # cache miss

        db_state = {"world_turn": 7, "locations": {}}
        ctx = _make_ctx(db_state)

        with patch("app.core.state_cache.service.get_redis", return_value=r):
            result = load_context_state("ctx-1", ctx)

        assert result == db_state

    def test_redis_unavailable_falls_back_to_db(self):
        """When Redis is None, the context's state_blob is returned."""
        from app.core.state_cache.service import load_context_state

        db_state = {"world_turn": 3}
        ctx = _make_ctx(db_state)

        with patch("app.core.state_cache.service.get_redis", return_value=None):
            result = load_context_state("ctx-1", ctx)

        assert result == db_state

    def test_redis_error_falls_back_to_db(self):
        """On Redis exception, fall back to context's state_blob without raising."""
        from app.core.state_cache.service import load_context_state

        r = MagicMock()
        r.get.side_effect = ConnectionError("Redis down")
        db_state = {"world_turn": 5}
        ctx = _make_ctx(db_state)

        with patch("app.core.state_cache.service.get_redis", return_value=r):
            result = load_context_state("ctx-1", ctx)

        assert result == db_state

    def test_returns_fresh_copy(self):
        """Modifying the returned dict (including nested objects) does not affect the original."""
        from app.core.state_cache.service import load_context_state

        db_state = {"world_turn": 1, "agents": {"a1": {"hp": 100}}}
        ctx = _make_ctx(db_state)

        with patch("app.core.state_cache.service.get_redis", return_value=None):
            result = load_context_state("ctx-1", ctx)

        # Both top-level and nested objects are independent (deep copy)
        result["agents"]["a1"]["hp"] = 999
        assert db_state["agents"]["a1"]["hp"] == 100


# ── Tests: save_context_state ─────────────────────────────────────────────────


class TestSaveContextState:
    def test_redis_unavailable_always_writes_db(self):
        """When Redis is None, state_blob is always assigned."""
        from app.core.state_cache.service import save_context_state

        ctx = _make_ctx({})
        new_state = {"world_turn": 10}

        with patch("app.core.state_cache.service.get_redis", return_value=None):
            wrote = save_context_state("ctx-1", new_state, ctx)

        assert wrote is True
        assert ctx.state_blob == new_state
        assert ctx.state_version == 1

    def test_force_persist_writes_db_even_with_redis(self):
        """force_persist=True bypasses the interval and writes to DB."""
        from app.core.state_cache.service import save_context_state

        r = MagicMock()
        r.set.return_value = True
        ctx = _make_ctx({})
        new_state = {"world_turn": 20}

        with patch("app.core.state_cache.service.get_redis", return_value=r), \
             patch("app.core.state_cache.service._get_persist_interval", return_value=100):
            wrote = save_context_state("ctx-1", new_state, ctx, force_persist=True)

        assert wrote is True
        assert ctx.state_blob == new_state

    def test_interval_1_always_writes_db(self):
        """With interval=1, every call writes to DB."""
        from app.core.state_cache.service import save_context_state

        r = MagicMock()
        r.set.return_value = True
        # incr returns incrementing integers
        r.incr.side_effect = [1, 2, 3, 4, 5]
        ctx = _make_ctx({})

        with patch("app.core.state_cache.service.get_redis", return_value=r), \
             patch("app.core.state_cache.service._get_persist_interval", return_value=1):
            for i in range(5):
                ctx2 = _make_ctx({})
                wrote = save_context_state("ctx-1", {"turn": i}, ctx2)
                assert wrote is True, f"Expected DB write on call {i}"

    def test_interval_5_writes_db_every_5_ticks(self):
        """With interval=5, DB is written on ticks 5, 10, 15, ..."""
        from app.core.state_cache.service import save_context_state

        r = MagicMock()
        r.set.return_value = True
        # Simulate 10 ticks
        r.incr.side_effect = list(range(1, 11))
        r.expire.return_value = True

        results = []
        with patch("app.core.state_cache.service.get_redis", return_value=r), \
             patch("app.core.state_cache.service._get_persist_interval", return_value=5):
            for i in range(10):
                ctx = _make_ctx({})
                wrote = save_context_state("ctx-1", {"turn": i}, ctx)
                results.append(wrote)

        # Writes on ticks 5 and 10 (indices 4 and 9)
        assert results == [False, False, False, False, True,
                           False, False, False, False, True]

    def test_redis_write_failure_falls_back_to_db(self):
        """If Redis.set() raises, we fall back to writing DB."""
        from app.core.state_cache.service import save_context_state

        r = MagicMock()
        r.set.side_effect = ConnectionError("Redis down")
        ctx = _make_ctx({})

        with patch("app.core.state_cache.service.get_redis", return_value=r):
            wrote = save_context_state("ctx-1", {"turn": 1}, ctx)

        assert wrote is True
        assert ctx.state_blob == {"turn": 1}

    def test_no_db_write_does_not_touch_context_obj(self):
        """When interval not reached, context_obj.state_blob is NOT modified."""
        from app.core.state_cache.service import save_context_state

        r = MagicMock()
        r.set.return_value = True
        r.incr.return_value = 1  # tick 1 of 5 → no DB write
        r.expire.return_value = True

        original_state = {"world_turn": 0}
        ctx = _make_ctx(original_state)

        with patch("app.core.state_cache.service.get_redis", return_value=r), \
             patch("app.core.state_cache.service._get_persist_interval", return_value=5):
            wrote = save_context_state("ctx-1", {"world_turn": 99}, ctx)

        assert wrote is False
        # state_blob must NOT have been changed
        assert ctx.state_blob == original_state
        assert ctx.state_version == 0

    def test_writes_compressed_bytes_to_redis(self):
        """save_context_state stores the state compressed in Redis."""
        from app.core.state_cache.service import save_context_state, _compress

        r = MagicMock()
        r.set.return_value = True
        ctx = _make_ctx({})
        state = {"world_turn": 7, "locations": {"A1": {}}}

        with patch("app.core.state_cache.service.get_redis", return_value=r), \
             patch("app.core.state_cache.service._get_persist_interval", return_value=1):
            save_context_state("ctx-1", state, ctx)

        # First positional arg to r.set is the key, second is the value
        call_args = r.set.call_args
        stored_bytes = call_args[0][1]
        # Must decompress to the original state
        recovered = json.loads(zlib.decompress(stored_bytes))
        assert recovered == state


# ── Tests: invalidate_context_state ──────────────────────────────────────────


class TestInvalidateContextState:
    def test_deletes_redis_keys(self):
        from app.core.state_cache.service import invalidate_context_state

        r = MagicMock()
        with patch("app.core.state_cache.service.get_redis", return_value=r):
            invalidate_context_state("ctx-abc")

        r.delete.assert_called_once()
        keys_deleted = r.delete.call_args[0]
        assert "ctx:state:ctx-abc" in keys_deleted
        assert "ctx:ticks:ctx-abc" in keys_deleted

    def test_noop_when_redis_unavailable(self):
        from app.core.state_cache.service import invalidate_context_state

        with patch("app.core.state_cache.service.get_redis", return_value=None):
            # Must not raise
            invalidate_context_state("ctx-abc")
