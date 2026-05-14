"""Tests for scripts/zone_stalkers/analyze_npc_log_export.py (PR7 Part 4).

Uses small synthetic in-memory fixtures — no real large logs required.
"""
from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Path setup — add the scripts directory to sys.path so we can import the
# module directly without installing it as a package.
# Repository layout: backend/tests/scripts/ → root is 3 levels up.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent
for _ in range(4):  # walk up at most 4 levels looking for scripts/zone_stalkers
    candidate = _REPO_ROOT / "scripts" / "zone_stalkers"
    if candidate.is_dir():
        SCRIPTS_DIR = candidate
        break
    _REPO_ROOT = _REPO_ROOT.parent
else:
    raise RuntimeError("Could not locate scripts/zone_stalkers relative to test file")

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import analyze_npc_log_export as ana  # noqa: E402  (after sys.path tweak)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent(
    agent_id: str = "bot1",
    *,
    is_alive: bool = True,
    hp: float = 100.0,
    memory_records: dict | None = None,
    active_plan: dict | None = None,
    inventory: list | None = None,
) -> dict[str, Any]:
    agent: dict[str, Any] = {
        "id": agent_id,
        "is_alive": is_alive,
        "hp": hp,
        "max_hp": 100.0,
        "hunger": 0.0,
        "thirst": 0.0,
        "money": 100.0,
        "faction": "loner",
        "inventory": inventory or [],
    }
    if memory_records is not None:
        agent["memory_v3"] = {"records": memory_records}
    if active_plan is not None:
        agent["active_plan_v3"] = active_plan
    return agent


def _make_zip_bytes(agents: list[dict[str, Any]]) -> bytes:
    """Return bytes of a ZIP containing one JSON file with the given agents."""
    agents_dict = {a["id"]: a for a in agents}
    payload = {"agents": agents_dict}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("snapshot.json", json.dumps(payload))
    return buf.getvalue()


def _write_zip(tmp_path: Path, agents: list[dict[str, Any]], name: str = "export.zip") -> Path:
    p = tmp_path / name
    p.write_bytes(_make_zip_bytes(agents))
    return p


def _memory_with_tsf(reason: str) -> dict:
    """Build a memory_v3 dict with one trade_sell_failed record."""
    return {
        "records": {
            "rec_tsf_1": {
                "kind": "trade_sell_failed",
                "details": {"reason": reason, "item_types": []},
            }
        }
    }


# ---------------------------------------------------------------------------
# test_analyzer_counts_memory_pressure
# ---------------------------------------------------------------------------

class TestMemoryPressure:
    def test_zero_records_for_empty_memory(self) -> None:
        agent = _make_agent("a1")
        m = ana.compute_agent_metrics(agent, world_turn=0)
        assert m["memory_record_count"] == 0
        assert m["memory_near_limit"] is False

    def test_counts_records_dict(self) -> None:
        records = {f"r{i}": {"kind": "seen"} for i in range(10)}
        agent = _make_agent("a1", memory_records=records)
        m = ana.compute_agent_metrics(agent, world_turn=0)
        assert m["memory_record_count"] == 10

    def test_counts_records_list(self) -> None:
        records_list = [{"kind": "seen"} for _ in range(5)]
        agent = _make_agent("a1")
        agent["memory_v3"] = {"records": records_list}
        m = ana.compute_agent_metrics(agent, world_turn=0)
        assert m["memory_record_count"] == 5

    def test_near_limit_flag(self) -> None:
        # 401 records > 80% of 500
        records = {f"r{i}": {} for i in range(401)}
        agent = _make_agent("a1", memory_records=records)
        m = ana.compute_agent_metrics(agent, world_turn=0)
        assert m["memory_near_limit"] is True

    def test_fleet_memory_near_limit_count(self, tmp_path: Path) -> None:
        # 3 agents: 2 near limit, 1 not
        heavy = {f"r{i}": {} for i in range(410)}
        light = {f"r{i}": {} for i in range(10)}
        agents = [
            _make_agent("a1", memory_records=heavy),
            _make_agent("a2", memory_records=heavy),
            _make_agent("a3", memory_records=light),
        ]
        zp = _write_zip(tmp_path, agents)
        result = ana.analyze_sources([zp])
        assert result["fleet"]["memory_near_limit_count"] == 2


# ---------------------------------------------------------------------------
# test_analyzer_detects_trade_sell_failed_empty_item_types
# ---------------------------------------------------------------------------

class TestTradeSellFailureDetection:
    def test_no_tsf_when_no_memory(self) -> None:
        agent = _make_agent("a1")
        m = ana.compute_agent_metrics(agent, world_turn=0)
        assert m["trade_sell_failure_reasons"] == []

    def test_detects_no_sellable_items_reason(self) -> None:
        agent = _make_agent("a1", memory_records=_memory_with_tsf("no_sellable_items")["records"])
        m = ana.compute_agent_metrics(agent, world_turn=0)
        assert "no_sellable_items" in m["trade_sell_failure_reasons"]

    def test_detects_no_items_sold_reason(self) -> None:
        agent = _make_agent("a1", memory_records=_memory_with_tsf("no_items_sold")["records"])
        m = ana.compute_agent_metrics(agent, world_turn=0)
        assert "no_items_sold" in m["trade_sell_failure_reasons"]

    def test_fleet_tsf_distribution(self, tmp_path: Path) -> None:
        agents = [
            _make_agent("a1", memory_records=_memory_with_tsf("no_sellable_items")["records"]),
            _make_agent("a2", memory_records=_memory_with_tsf("no_sellable_items")["records"]),
            _make_agent("a3", memory_records=_memory_with_tsf("trader_no_money")["records"]),
            _make_agent("a4"),
        ]
        zp = _write_zip(tmp_path, agents)
        result = ana.analyze_sources([zp])
        dist = result["fleet"]["trade_sell_failure_distribution"]
        assert dist.get("no_sellable_items", 0) == 2
        assert dist.get("trader_no_money", 0) == 1

    def test_anomaly_reported_for_no_sellable_items(self, tmp_path: Path) -> None:
        agents = [_make_agent("a1", memory_records=_memory_with_tsf("no_sellable_items")["records"])]
        zp = _write_zip(tmp_path, agents)
        result = ana.analyze_sources([zp])
        kinds = [a["kind"] for a in result["anomalies"]]
        assert "no_sellable_items_cooldown" in kinds

    def test_counts_trade_sell_failed_empty_item_types_metric(self, tmp_path: Path) -> None:
        recs = {
            "r1": {"kind": "trade_sell_failed", "details": {"reason": "no_items_sold", "item_types": []}},
            "r2": {"kind": "trade_sell_failed", "details": {"reason": "trader_no_money", "item_types": ["soul"]}},
            "r3": {"kind": "trade_sell_failed", "details": {"reason": "no_sellable_items"}},
        }
        agents = [_make_agent("a1", memory_records=recs)]
        zp = _write_zip(tmp_path, agents)
        result = ana.analyze_sources([zp])
        assert result["fleet"]["trade_sell_failed_empty_item_types"] == 2


# ---------------------------------------------------------------------------
# test_analyzer_detects_corpse_seen_alive_agent (zombie detection)
# ---------------------------------------------------------------------------

class TestZombieDetection:
    def test_zombie_agent_hp_zero_is_alive(self) -> None:
        agent = _make_agent("a1", is_alive=True, hp=0.0)
        m = ana.compute_agent_metrics(agent, world_turn=0)
        assert m["zombie"] is True

    def test_normal_alive_agent_not_zombie(self) -> None:
        agent = _make_agent("a1", is_alive=True, hp=80.0)
        m = ana.compute_agent_metrics(agent, world_turn=0)
        assert m["zombie"] is False

    def test_dead_agent_not_zombie(self) -> None:
        agent = _make_agent("a1", is_alive=False, hp=0.0)
        m = ana.compute_agent_metrics(agent, world_turn=0)
        assert m["zombie"] is False

    def test_zombie_anomaly_reported(self, tmp_path: Path) -> None:
        agents = [_make_agent("zombie1", is_alive=True, hp=0.0)]
        zp = _write_zip(tmp_path, agents)
        result = ana.analyze_sources([zp])
        kinds = [a["kind"] for a in result["anomalies"]]
        assert "zombie_agent" in kinds

    def test_fleet_zombie_count(self, tmp_path: Path) -> None:
        agents = [
            _make_agent("z1", is_alive=True, hp=0.0),
            _make_agent("z2", is_alive=True, hp=0.0),
            _make_agent("ok1", is_alive=True, hp=50.0),
        ]
        zp = _write_zip(tmp_path, agents)
        result = ana.analyze_sources([zp])
        assert result["fleet"]["zombie_count"] == 2


# ---------------------------------------------------------------------------
# test_analyzer_detects_pending_trade_stuck_state
# ---------------------------------------------------------------------------

class TestStuckPlanDetection:
    def _make_pending_plan(self, step_kind: str, started_turn: int) -> dict:
        return {
            "status": "active",
            "repair_count": 0,
            "current_step_index": 0,
            "steps": [
                {"kind": step_kind, "status": "pending", "started_turn": started_turn}
            ],
        }

    def test_no_plan_not_stuck(self) -> None:
        agent = _make_agent("a1")
        m = ana.compute_agent_metrics(agent, world_turn=100)
        assert m["plan_stuck"] is False

    def test_pending_trade_sell_within_timeout_not_stuck(self) -> None:
        plan = self._make_pending_plan("trade_sell_item", started_turn=96)
        agent = _make_agent("a1", active_plan=plan)
        m = ana.compute_agent_metrics(agent, world_turn=100)
        # age = 4 ≤ 5, not stuck
        assert m["plan_stuck"] is False

    def test_pending_trade_sell_exceeded_is_stuck(self) -> None:
        plan = self._make_pending_plan("trade_sell_item", started_turn=90)
        agent = _make_agent("a1", active_plan=plan)
        # age = 10 > 5
        m = ana.compute_agent_metrics(agent, world_turn=100)
        assert m["plan_stuck"] is True

    def test_pending_trade_buy_exceeded_is_stuck(self) -> None:
        plan = self._make_pending_plan("trade_buy_item", started_turn=80)
        agent = _make_agent("a1", active_plan=plan)
        m = ana.compute_agent_metrics(agent, world_turn=100)
        assert m["plan_stuck"] is True

    def test_fleet_stuck_plan_count(self, tmp_path: Path) -> None:
        stuck_plan = self._make_pending_plan("trade_sell_item", started_turn=1)
        agents = [
            _make_agent("s1", active_plan=stuck_plan),
            _make_agent("s2", active_plan=stuck_plan),
            _make_agent("ok", active_plan=self._make_pending_plan("trade_sell_item", started_turn=98)),
        ]
        zp = _write_zip(tmp_path, agents)
        result = ana.analyze_sources([zp], world_turn=100)
        assert result["fleet"]["stuck_plan_count"] == 2

    def test_stuck_anomaly_detail(self, tmp_path: Path) -> None:
        stuck_plan = self._make_pending_plan("trade_sell_item", started_turn=1)
        agents = [_make_agent("a1", active_plan=stuck_plan)]
        zp = _write_zip(tmp_path, agents)
        result = ana.analyze_sources([zp], world_turn=100)
        plan_stuck_anomalies = [a for a in result["anomalies"] if a["kind"] == "plan_stuck"]
        assert len(plan_stuck_anomalies) == 1
        assert "trade_sell_item" in plan_stuck_anomalies[0]["detail"]


# ---------------------------------------------------------------------------
# test_analyzer_compares_two_batches
# ---------------------------------------------------------------------------

class TestComparisonMode:
    def test_compute_delta_basic(self) -> None:
        before = {"alive_count": 10, "stuck_plan_count": 3, "zombie_count": 1}
        after = {"alive_count": 8, "stuck_plan_count": 1, "zombie_count": 0}
        delta = ana.compute_delta(before, after)
        assert delta["alive_count"]["delta"] == -2
        assert delta["stuck_plan_count"]["delta"] == -2
        assert delta["zombie_count"]["delta"] == -1

    def test_compute_delta_missing_key(self) -> None:
        before = {"alive_count": 5}
        after = {"alive_count": 7}
        delta = ana.compute_delta(before, after)
        assert delta["alive_count"]["delta"] == 2
        assert delta["alive_count"]["before"] == 5
        assert delta["alive_count"]["after"] == 7

    def test_compare_two_zips_produces_delta(self, tmp_path: Path) -> None:
        agents_before = [_make_agent(f"b{i}") for i in range(4)]
        # 2 stuck agents in after
        stuck_plan = {
            "status": "active", "repair_count": 0, "current_step_index": 0,
            "steps": [{"kind": "trade_sell_item", "status": "pending", "started_turn": 1}],
        }
        agents_after = [
            _make_agent("a1", active_plan=stuck_plan),
            _make_agent("a2", active_plan=stuck_plan),
            _make_agent("a3"),
        ]
        zip_before = _write_zip(tmp_path, agents_before, "before.zip")
        zip_after = _write_zip(tmp_path, agents_after, "after.zip")

        before_res = ana.analyze_sources([zip_before], world_turn=50)
        after_res = ana.analyze_sources([zip_after], world_turn=50)
        delta = ana.compute_delta(before_res["fleet"], after_res["fleet"])

        assert "stuck_plan_count" in delta
        assert delta["stuck_plan_count"]["delta"] == 2.0  # 0 → 2

    def test_compare_mode_in_main(self, tmp_path: Path) -> None:
        agents_before = [_make_agent("b1")]
        agents_after = [_make_agent("a1")]
        zip_before = _write_zip(tmp_path, agents_before, "b.zip")
        zip_after = _write_zip(tmp_path, agents_after, "a.zip")
        rc = ana.main(["--compare", str(zip_before), str(zip_after), "--quiet"])
        assert rc == 0  # no violations

    def test_multiple_sources_include_window_deltas(self, tmp_path: Path) -> None:
        before = [_make_agent("a1", memory_records={"r1": {"kind": "trade_sell_failed", "details": {"item_types": []}}})]
        after = [_make_agent("a1", memory_records={})]
        z1 = _write_zip(tmp_path, before, "s1.zip")
        z2 = _write_zip(tmp_path, after, "s2.zip")
        result = ana.analyze_sources([z1, z2])
        assert "window_deltas" in result["fleet"]
        assert isinstance(result["fleet"]["window_deltas"], list)


# ---------------------------------------------------------------------------
# test_analyzer_threshold_failure
# ---------------------------------------------------------------------------

class TestThresholdValidation:
    def test_no_violations_passes(self) -> None:
        fleet = {"zombie_count": 0, "stuck_plan_count": 0}
        thresholds = {
            "zombie_count": {"op": "max", "limit": 0},
            "stuck_plan_count": {"op": "max", "limit": 2},
        }
        violations = ana.validate_thresholds(fleet, [], thresholds)
        assert violations == []

    def test_violation_when_metric_exceeds_max(self) -> None:
        fleet = {"zombie_count": 3, "stuck_plan_count": 0}
        thresholds = {"zombie_count": {"op": "max", "limit": 0}}
        violations = ana.validate_thresholds(fleet, [], thresholds)
        assert len(violations) == 1
        assert "zombie_count" in violations[0]

    def test_anomaly_threshold_violation_included(self) -> None:
        fleet: dict[str, Any] = {}
        anomaly = {
            "kind": "zombie_agent",
            "agent_id": "z1",
            "detail": "hp=0",
            "threshold_violated": True,
        }
        violations = ana.validate_thresholds(fleet, [anomaly], {})
        assert len(violations) == 1
        assert "zombie_agent" in violations[0]

    def test_fail_on_threshold_exits_1(self, tmp_path: Path) -> None:
        # zombie agent should trigger violation with threshold {"zombie_count": {"op":"max","limit":0}}
        th_file = tmp_path / "th.json"
        th_file.write_text(json.dumps({"zombie_count": {"op": "max", "limit": 0}}))
        agents = [_make_agent("z1", is_alive=True, hp=0.0)]
        zp = _write_zip(tmp_path, agents)
        rc = ana.main([str(zp), "--thresholds", str(th_file), "--fail-on-threshold", "--quiet"])
        assert rc == 1

    def test_no_fail_without_flag(self, tmp_path: Path) -> None:
        th_file = tmp_path / "th.json"
        th_file.write_text(json.dumps({"zombie_count": {"op": "max", "limit": 0}}))
        agents = [_make_agent("z1", is_alive=True, hp=0.0)]
        zp = _write_zip(tmp_path, agents)
        # Without --fail-on-threshold flag, exit code should be 0 even with violations
        rc = ana.main([str(zp), "--thresholds", str(th_file), "--quiet"])
        assert rc == 0

    def test_input_flag_accepted(self, tmp_path: Path) -> None:
        agents = [_make_agent("a1")]
        zp = _write_zip(tmp_path, agents)
        rc = ana.main(["--input", str(zp), "--quiet"])
        assert rc == 0

    def test_plain_numeric_threshold_is_validated(self) -> None:
        fleet = {"max_stuck_states_total": 3}
        thresholds = {"max_stuck_states_total": 2}
        violations = ana.validate_thresholds(fleet, [], thresholds)
        assert violations
        assert "max_stuck_states_total" in violations[0]


class TestAdditionalPr10Metrics:
    def test_corpse_seen_alive_agent_count_metric(self, tmp_path: Path) -> None:
        observer_recs = {
            "r1": {"kind": "corpse_seen", "details": {"action_kind": "corpse_seen", "dead_agent_id": "alive_1"}}
        }
        agents = [
            _make_agent("observer", memory_records=observer_recs),
            _make_agent("alive_1", is_alive=True, hp=100.0),
        ]
        zp = _write_zip(tmp_path, agents)
        result = ana.analyze_sources([zp])
        assert result["fleet"]["corpse_seen_alive_agent_count"] == 1

    def test_memory_write_dropped_and_evictions_totals(self, tmp_path: Path) -> None:
        agent = _make_agent("a1")
        agent["memory_v3"] = {
            "records": {},
            "stats": {"memory_write_dropped": 4, "memory_evictions": 7},
        }
        zp = _write_zip(tmp_path, [agent])
        result = ana.analyze_sources([zp])
        assert result["fleet"]["memory_write_dropped_total"] == 4
        assert result["fleet"]["memory_evictions_total"] == 7

    def test_context_builder_scan_ratio_metric(self, tmp_path: Path) -> None:
        agent = _make_agent("a1")
        agent["brain_context_metrics"] = {
            "context_builder_memory_scan_records": 50,
            "context_builder_calls": 10,
        }
        zp = _write_zip(tmp_path, [agent])
        result = ana.analyze_sources([zp])
        assert result["fleet"]["max_context_builder_memory_scan_records_per_agent_decision"] == 5.0

    def test_knowledge_first_fallback_and_rate_metrics(self, tmp_path: Path) -> None:
        agent = _make_agent("a1")
        agent["brain_context_metrics"] = {
            "target_belief_memory_fallbacks": 2,
            "context_builder_memory_fallbacks": 3,
        }
        agent["memory_v3"] = {
            "records": {},
            "stats": {"memory_write_dropped": 10, "memory_evictions": 5},
        }
        zp = _write_zip(tmp_path, [agent])
        result = ana.analyze_sources([zp], world_turn=100)
        assert result["fleet"]["target_belief_memory_fallbacks"] == 2
        assert result["fleet"]["context_builder_memory_fallbacks"] == 3
        assert "memory_evictions_per_tick" in result["fleet"]
        assert "memory_drops_per_tick" in result["fleet"]


# ---------------------------------------------------------------------------
# test_analyzer_markdown_and_json_output
# ---------------------------------------------------------------------------

class TestOutputFiles:
    def test_markdown_report_written(self, tmp_path: Path) -> None:
        agents = [_make_agent("a1")]
        zp = _write_zip(tmp_path, agents)
        out_md = tmp_path / "report.md"
        rc = ana.main([str(zp), "--out", str(out_md), "--quiet"])
        assert rc == 0
        assert out_md.exists()
        content = out_md.read_text()
        assert "Zone Stalkers" in content or "Fleet" in content

    def test_json_out_written(self, tmp_path: Path) -> None:
        agents = [_make_agent("a1")]
        zp = _write_zip(tmp_path, agents)
        out_json = tmp_path / "metrics.json"
        rc = ana.main([str(zp), "--json-out", str(out_json), "--quiet"])
        assert rc == 0
        assert out_json.exists()
        data = json.loads(out_json.read_text())
        assert "fleet" in data
        assert data["fleet"]["agent_count"] == 1
