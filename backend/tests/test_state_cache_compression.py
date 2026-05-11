import zlib

from app.core.state_cache.service import _compress, _decompress


def test_compress_decompress_roundtrip():
    state = {"a": 1, "b": {"x": [1, 2, 3]}}
    blob = _compress(state)
    restored = _decompress(blob)
    assert restored == state


def test_compress_returns_zlib_payload():
    state = {"hello": "world"}
    blob = _compress(state)
    # zlib-compressed payload should be decodable by zlib directly.
    raw = zlib.decompress(blob)
    assert b"hello" in raw
