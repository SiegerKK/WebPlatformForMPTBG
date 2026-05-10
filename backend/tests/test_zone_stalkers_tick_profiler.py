"""Tests for TickProfiler (CPU PR1)."""
import time
import pytest
from app.games.zone_stalkers.performance.tick_profiler import TickProfiler


def test_tick_profiler_records_sections_and_counters():
    p = TickProfiler()
    with p.section("phase_a"):
        time.sleep(0.001)
    p.inc("my_counter")
    p.inc("my_counter", 4)
    result = p.to_dict()
    assert "phase_a" in result["sections_ms"]
    assert result["sections_ms"]["phase_a"] >= 0
    assert result["counters"]["my_counter"] == 5


def test_tick_profiler_disabled_records_nothing():
    p = TickProfiler(enabled=False)
    with p.section("noop"):
        time.sleep(0.001)
    p.inc("x")
    p.set_counter("y", 99)
    result = p.to_dict()
    assert result["sections_ms"] == {}
    assert result["counters"] == {}


def test_tick_profiler_section_accumulates():
    p = TickProfiler()
    with p.section("step"):
        time.sleep(0.001)
    with p.section("step"):
        time.sleep(0.001)
    result = p.to_dict()
    # Both calls should be summed
    assert result["sections_ms"]["step"] >= 0
    # Should only have one entry, not two
    assert list(result["sections_ms"].keys()) == ["step"]


def test_tick_profiler_to_dict_structure():
    p = TickProfiler()
    with p.section("alpha"):
        pass
    p.set_counter("beta", 7)
    result = p.to_dict()
    assert "sections_ms" in result
    assert "counters" in result
    assert isinstance(result["sections_ms"], dict)
    assert isinstance(result["counters"], dict)
    assert result["counters"]["beta"] == 7


def test_tick_profiler_set_counter():
    p = TickProfiler()
    p.set_counter("total", 42)
    assert p.to_dict()["counters"]["total"] == 42
    p.set_counter("total", 10)
    assert p.to_dict()["counters"]["total"] == 10


def test_tick_profiler_sections_rounded():
    p = TickProfiler()
    with p.section("s"):
        time.sleep(0.0012345)
    val = p.to_dict()["sections_ms"]["s"]
    # Should be rounded to 3 decimal places
    assert val == round(val, 3)
