#!/usr/bin/env python3
"""analyze_npc_log_export.py — Zone Stalkers NPC log export analyzer (PR7 Part 4).

Reads ZIP archives produced by the platform's NPC debug-export endpoint.
Each ZIP contains one or more JSON files with agent state snapshots.

Usage
-----
    python analyze_npc_log_export.py export.zip [export2.zip ...]
        [--thresholds pr10_validation_thresholds.json]
        [--compare export_before.zip export_after.zip]
        [--out report.md]
        [--json-out metrics.json]
        [--quiet]

Capabilities
------------
* Parse one or many ZIP export files.
* Extract per-agent metrics: memory record counts, active plan status, vital
  stats (hp / hunger / thirst / radiation), faction, current goal, inventory
  artifact count.
* Compute fleet-level aggregates: alive/dead ratio, average vital stats,
  plan completion/abort/repair rates, trade-sell failure distribution.
* Detect common anomalies:
    - Agents with is_alive=True but hp==0 (zombie agents).
    - Agents with stale trade-sell cooldowns blocking all sell attempts.
    - Active plans stuck in PENDING for >5 turns (configurable via thresholds).
    - Memory_v3 approaching the MEMORY_V3_MAX_RECORDS limit.
* Support optional threshold file (JSON) for pass/fail assertions; exits with
  code 1 when any threshold is violated.
* Support comparison mode (--compare before after) that prints deltas for key
  metrics.
* Emit a GitHub-friendly Markdown report and/or a raw JSON metrics dump.
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MEMORY_V3_MAX_RECORDS = 500       # hard ceiling in tick_constants.py
MEMORY_WARN_RATIO = 0.80          # warn when > 80 % full
ACTIVE_PLAN_PENDING_TIMEOUT_TURNS = 5  # from decision/constants.py

FIELD_AGENT_ID = "id"
FIELD_IS_ALIVE = "is_alive"
FIELD_HP = "hp"
FIELD_MAX_HP = "max_hp"
FIELD_HUNGER = "hunger"
FIELD_THIRST = "thirst"
FIELD_RADIATION = "radiation"
FIELD_SLEEPINESS = "sleepiness"
FIELD_MONEY = "money"
FIELD_FACTION = "faction"
FIELD_MEMORY_V3 = "memory_v3"
FIELD_ACTIVE_PLAN = "active_plan_v3"
FIELD_INVENTORY = "inventory"
FIELD_GLOBAL_GOAL = "global_goal"
FIELD_CURRENT_GOAL = "current_goal"


# ---------------------------------------------------------------------------
# ZIP / file loading
# ---------------------------------------------------------------------------

def _load_json_bytes(data: bytes) -> Any:
    try:
        return json.loads(data.decode("utf-8", errors="replace"))
    except Exception:
        return None


def _iter_zip_agents(zip_path: Path) -> Iterator[dict[str, Any]]:
    """Yield agent dicts from all JSON files inside a ZIP archive."""
    if not zip_path.exists():
        raise FileNotFoundError(f"ZIP not found: {zip_path}")
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if not name.endswith(".json"):
                continue
            raw = zf.read(name)
            payload = _load_json_bytes(raw)
            if not isinstance(payload, dict):
                continue
            # Payload may be a single agent dict or {"agents": {...}} wrapper
            if "agents" in payload and isinstance(payload["agents"], dict):
                for agent in payload["agents"].values():
                    if isinstance(agent, dict):
                        yield agent
            elif FIELD_AGENT_ID in payload or "name" in payload:
                yield payload


def _iter_json_file_agents(json_path: Path) -> Iterator[dict[str, Any]]:
    """Yield agent dicts from a plain JSON file (for testing convenience)."""
    if not json_path.exists():
        raise FileNotFoundError(f"JSON not found: {json_path}")
    payload = _load_json_bytes(json_path.read_bytes())
    if isinstance(payload, dict):
        if "agents" in payload and isinstance(payload["agents"], dict):
            for agent in payload["agents"].values():
                if isinstance(agent, dict):
                    yield agent
        elif FIELD_AGENT_ID in payload or "name" in payload:
            yield payload
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item


def iter_agents_from_source(source: Path) -> Iterator[dict[str, Any]]:
    """Yield agents from a ZIP or JSON file."""
    suffix = source.suffix.lower()
    if suffix == ".zip":
        yield from _iter_zip_agents(source)
    elif suffix == ".json":
        yield from _iter_json_file_agents(source)
    else:
        raise ValueError(f"Unsupported source format: {source}")


# ---------------------------------------------------------------------------
# Per-agent metrics
# ---------------------------------------------------------------------------

def _memory_record_count(agent: dict[str, Any]) -> int:
    mem = agent.get(FIELD_MEMORY_V3)
    if not isinstance(mem, dict):
        return 0
    records = mem.get("records")
    if isinstance(records, dict):
        return len(records)
    if isinstance(records, list):
        return len(records)
    return 0


def _artifact_count(agent: dict[str, Any]) -> int:
    """Return number of artifact items in inventory (type must start with known prefix)."""
    try:
        from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES  # type: ignore
        art_types = frozenset(ARTIFACT_TYPES.keys())
    except ImportError:
        # Fallback: any item whose type name looks artifact-like
        art_types = None

    inventory = agent.get(FIELD_INVENTORY) or []
    if art_types:
        return sum(1 for i in inventory if isinstance(i, dict) and i.get("type") in art_types)
    return sum(1 for i in inventory if isinstance(i, dict) and "artifact" in str(i.get("type", "")).lower())


def _active_plan_summary(agent: dict[str, Any], world_turn: int) -> dict[str, Any]:
    """Return structured summary of agent's active plan."""
    ap = agent.get(FIELD_ACTIVE_PLAN)
    if not isinstance(ap, dict):
        return {"has_plan": False}

    step_list = ap.get("steps") or []
    current_idx = int(ap.get("current_step_index") or 0)
    current_step = step_list[current_idx] if 0 <= current_idx < len(step_list) else None

    pending_age: int | None = None
    if isinstance(current_step, dict) and current_step.get("status") == "pending":
        started = current_step.get("started_turn") or ap.get("created_turn") or world_turn
        pending_age = world_turn - int(started)

    return {
        "has_plan": True,
        "status": str(ap.get("status") or ""),
        "repair_count": int(ap.get("repair_count") or 0),
        "abort_reason": str(ap.get("abort_reason") or ""),
        "current_step_kind": str((current_step or {}).get("kind") or "") if current_step else "",
        "current_step_status": str((current_step or {}).get("status") or "") if current_step else "",
        "pending_age": pending_age,
    }


def _trade_sell_failure_reasons(agent: dict[str, Any]) -> list[str]:
    """Collect trade_sell_failed reasons from memory_v3."""
    mem = agent.get(FIELD_MEMORY_V3)
    if not isinstance(mem, dict):
        return []
    records = mem.get("records")
    if isinstance(records, dict):
        records_iter = records.values()
    elif isinstance(records, list):
        records_iter = records
    else:
        return []
    reasons = []
    for rec in records_iter:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("kind") or "") != "trade_sell_failed":
            continue
        details = rec.get("details") or {}
        reason = str(details.get("reason") or "")
        if reason:
            reasons.append(reason)
    return reasons


def compute_agent_metrics(agent: dict[str, Any], world_turn: int) -> dict[str, Any]:
    """Compute all metrics for a single agent."""
    agent_id = str(agent.get(FIELD_AGENT_ID) or agent.get("name") or "?")
    is_alive = bool(agent.get(FIELD_IS_ALIVE, True))
    hp = float(agent.get(FIELD_HP) or 0)
    max_hp = float(agent.get(FIELD_MAX_HP) or 100)
    hp_pct = hp / max_hp if max_hp > 0 else 0.0

    mem_count = _memory_record_count(agent)
    mem_warn = mem_count >= int(MEMORY_V3_MAX_RECORDS * MEMORY_WARN_RATIO)

    ap = _active_plan_summary(agent, world_turn)
    tsf_reasons = _trade_sell_failure_reasons(agent)
    pending_age = ap.get("pending_age")
    plan_stuck = (
        pending_age is not None
        and pending_age > ACTIVE_PLAN_PENDING_TIMEOUT_TURNS
    )

    zombie = is_alive and hp <= 0

    return {
        "agent_id": agent_id,
        "is_alive": is_alive,
        "hp": hp,
        "max_hp": max_hp,
        "hp_pct": round(hp_pct, 3),
        "hunger": float(agent.get(FIELD_HUNGER) or 0),
        "thirst": float(agent.get(FIELD_THIRST) or 0),
        "radiation": float(agent.get(FIELD_RADIATION) or 0),
        "sleepiness": float(agent.get(FIELD_SLEEPINESS) or 0),
        "money": float(agent.get(FIELD_MONEY) or 0),
        "faction": str(agent.get(FIELD_FACTION) or "unknown"),
        "memory_record_count": mem_count,
        "memory_near_limit": mem_warn,
        "artifact_count": _artifact_count(agent),
        "active_plan": ap,
        "plan_stuck": plan_stuck,
        "trade_sell_failure_reasons": tsf_reasons,
        "zombie": zombie,
        "global_goal": str(agent.get(FIELD_GLOBAL_GOAL) or ""),
        "current_goal": str(agent.get(FIELD_CURRENT_GOAL) or ""),
    }


# ---------------------------------------------------------------------------
# Fleet-level aggregation
# ---------------------------------------------------------------------------

def aggregate_fleet_metrics(
    agent_metrics: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute fleet-wide statistics from a list of per-agent metrics."""
    if not agent_metrics:
        return {"agent_count": 0}

    alive = [m for m in agent_metrics if m["is_alive"]]
    dead = [m for m in agent_metrics if not m["is_alive"]]
    zombies = [m for m in agent_metrics if m["zombie"]]
    stuck = [m for m in agent_metrics if m["plan_stuck"]]
    mem_warn = [m for m in agent_metrics if m["memory_near_limit"]]

    def avg(seq: list, key: str) -> float:
        vals = [m[key] for m in seq if m.get(key) is not None]
        return round(sum(vals) / len(vals), 3) if vals else 0.0

    # Active plan status distribution
    plan_status_counts: Counter[str] = Counter()
    for m in agent_metrics:
        ap = m.get("active_plan") or {}
        if ap.get("has_plan"):
            plan_status_counts[str(ap.get("status") or "unknown")] += 1
        else:
            plan_status_counts["no_plan"] += 1

    # Trade-sell failure distribution
    tsf_counter: Counter[str] = Counter()
    for m in agent_metrics:
        for reason in m.get("trade_sell_failure_reasons") or []:
            tsf_counter[reason] += 1

    # Faction distribution
    faction_counter: Counter[str] = Counter(m["faction"] for m in agent_metrics)

    # Repair count distribution
    repair_counts: list[int] = [
        int((m.get("active_plan") or {}).get("repair_count") or 0)
        for m in agent_metrics
        if (m.get("active_plan") or {}).get("has_plan")
    ]
    avg_repairs = round(sum(repair_counts) / len(repair_counts), 3) if repair_counts else 0.0
    max_repairs = max(repair_counts, default=0)

    return {
        "agent_count": len(agent_metrics),
        "alive_count": len(alive),
        "dead_count": len(dead),
        "alive_pct": round(len(alive) / len(agent_metrics), 3),
        "zombie_count": len(zombies),
        "stuck_plan_count": len(stuck),
        "memory_near_limit_count": len(mem_warn),
        "avg_hp_pct": avg(alive, "hp_pct"),
        "avg_hunger": avg(alive, "hunger"),
        "avg_thirst": avg(alive, "thirst"),
        "avg_radiation": avg(alive, "radiation"),
        "avg_money": avg(alive, "money"),
        "avg_artifact_count": avg(alive, "artifact_count"),
        "avg_repair_count": avg_repairs,
        "max_repair_count": max_repairs,
        "plan_status_distribution": dict(plan_status_counts),
        "trade_sell_failure_distribution": dict(tsf_counter),
        "faction_distribution": dict(faction_counter),
    }


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------

def detect_anomalies(
    agent_metrics: list[dict[str, Any]],
    thresholds: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return a list of anomaly dicts for reporting."""
    th = thresholds or {}
    anomalies: list[dict[str, Any]] = []

    max_zombie = int(th.get("max_zombie_agents", 0))
    max_stuck = int(th.get("max_stuck_plan_agents", 0))
    max_mem_warn = int(th.get("max_memory_near_limit_agents", 5))

    for m in agent_metrics:
        aid = m["agent_id"]
        if m["zombie"]:
            anomalies.append({
                "kind": "zombie_agent",
                "agent_id": aid,
                "detail": f"is_alive=True but hp={m['hp']}",
                "threshold_violated": max_zombie < 1,
            })
        if m["plan_stuck"]:
            ap = m.get("active_plan") or {}
            anomalies.append({
                "kind": "plan_stuck",
                "agent_id": aid,
                "detail": f"step={ap.get('current_step_kind')} pending_age={ap.get('pending_age')}",
                "threshold_violated": max_stuck < 1,
            })
        if m["memory_near_limit"]:
            anomalies.append({
                "kind": "memory_near_limit",
                "agent_id": aid,
                "detail": f"memory_record_count={m['memory_record_count']}/{MEMORY_V3_MAX_RECORDS}",
                "threshold_violated": False,  # warning only
            })
        if "no_sellable_items" in m.get("trade_sell_failure_reasons", []):
            anomalies.append({
                "kind": "no_sellable_items_cooldown",
                "agent_id": aid,
                "detail": "Agent has no_sellable_items trade-sell failure in memory",
                "threshold_violated": False,
            })

    return anomalies


# ---------------------------------------------------------------------------
# Threshold validation
# ---------------------------------------------------------------------------

def validate_thresholds(
    fleet: dict[str, Any],
    anomalies: list[dict[str, Any]],
    thresholds: dict[str, Any],
) -> list[str]:
    """Return list of violation messages (empty = pass)."""
    violations: list[str] = []

    def _check(metric: str, op: str, limit: float) -> None:
        value = fleet.get(metric)
        if value is None:
            return
        fail = False
        if op == "max" and float(value) > limit:
            fail = True
        elif op == "min" and float(value) < limit:
            fail = True
        if fail:
            violations.append(f"{metric} {op}={limit} violated: actual={value}")

    for key, spec in thresholds.items():
        if not isinstance(spec, dict):
            continue
        op = str(spec.get("op") or "max")
        limit = float(spec.get("limit") or 0)
        _check(key, op, limit)

    threshold_violations = [a for a in anomalies if a.get("threshold_violated")]
    for a in threshold_violations:
        violations.append(f"anomaly/{a['kind']} for agent {a['agent_id']}: {a['detail']}")

    return violations


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def render_markdown_report(
    sources: list[Path],
    fleet: dict[str, Any],
    anomalies: list[dict[str, Any]],
    violations: list[str],
    *,
    delta: dict[str, Any] | None = None,
) -> str:
    """Render a Markdown report string."""
    lines: list[str] = []
    lines.append("# Zone Stalkers NPC Log Analysis")
    lines.append("")
    lines.append(f"**Sources:** {', '.join(str(s) for s in sources)}")
    lines.append("")

    lines.append("## Fleet Overview")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|---|---|")
    for k, v in fleet.items():
        if isinstance(v, dict):
            continue
        lines.append(f"| {k} | {v} |")
    lines.append("")

    if fleet.get("plan_status_distribution"):
        lines.append("### Active Plan Status Distribution")
        lines.append("")
        for status, count in sorted(fleet["plan_status_distribution"].items()):
            lines.append(f"- `{status}`: {count}")
        lines.append("")

    if fleet.get("trade_sell_failure_distribution"):
        lines.append("### Trade-Sell Failure Distribution")
        lines.append("")
        for reason, count in sorted(fleet["trade_sell_failure_distribution"].items()):
            lines.append(f"- `{reason}`: {count}")
        lines.append("")

    if fleet.get("faction_distribution"):
        lines.append("### Faction Distribution")
        lines.append("")
        for faction, count in sorted(fleet["faction_distribution"].items()):
            lines.append(f"- `{faction}`: {count}")
        lines.append("")

    if anomalies:
        lines.append("## Anomalies")
        lines.append("")
        for a in anomalies:
            icon = "🔴" if a.get("threshold_violated") else "⚠️"
            lines.append(f"{icon} **{a['kind']}** — agent `{a['agent_id']}`: {a['detail']}")
        lines.append("")
    else:
        lines.append("## Anomalies\n\n✅ No anomalies detected.\n")

    if delta:
        lines.append("## Comparison Delta")
        lines.append("")
        for k, d in sorted(delta.items()):
            if isinstance(d, dict) and "before" in d:
                lines.append(f"- **{k}**: {d['before']} → {d['after']} (Δ {d['delta']})")
        lines.append("")

    if violations:
        lines.append("## ❌ Threshold Violations")
        lines.append("")
        for v in violations:
            lines.append(f"- {v}")
        lines.append("")
    else:
        lines.append("## ✅ All Thresholds Passed\n")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Delta / comparison
# ---------------------------------------------------------------------------

def compute_delta(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    """Compute per-key deltas between two fleet metric dicts."""
    delta: dict[str, Any] = {}
    all_keys = set(before) | set(after)
    for k in sorted(all_keys):
        bv = before.get(k)
        av = after.get(k)
        if isinstance(bv, (int, float)) and isinstance(av, (int, float)):
            delta[k] = {"before": bv, "after": av, "delta": round(float(av) - float(bv), 4)}
    return delta


# ---------------------------------------------------------------------------
# Top-level analysis function (also used by tests)
# ---------------------------------------------------------------------------

def analyze_sources(
    sources: list[Path],
    world_turn: int = 0,
    thresholds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load agents from *sources* and return analysis results dict.

    Returns
    -------
    dict with keys:
        ``"fleet"`` — aggregate fleet metrics
        ``"agent_metrics"`` — per-agent metrics list
        ``"anomalies"`` — detected anomalies list
        ``"violations"`` — threshold violations list (empty = pass)
    """
    all_agent_metrics: list[dict[str, Any]] = []
    for source in sources:
        for agent in iter_agents_from_source(source):
            all_agent_metrics.append(compute_agent_metrics(agent, world_turn))

    fleet = aggregate_fleet_metrics(all_agent_metrics)
    anomalies = detect_anomalies(all_agent_metrics, thresholds)
    violations = validate_thresholds(fleet, anomalies, thresholds or {})
    return {
        "fleet": fleet,
        "agent_metrics": all_agent_metrics,
        "anomalies": anomalies,
        "violations": violations,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Zone Stalkers NPC log export analyzer (PR7).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("sources", nargs="*", metavar="EXPORT.ZIP",
                   help="ZIP or JSON export files to analyze.")
    p.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"),
                   help="Compare two exports and report deltas.")
    p.add_argument("--thresholds", metavar="THRESHOLDS.JSON",
                   help="JSON file with pass/fail threshold definitions.")
    p.add_argument("--out", metavar="REPORT.MD",
                   help="Write Markdown report to this file.")
    p.add_argument("--json-out", metavar="METRICS.JSON",
                   help="Write raw metrics JSON to this file.")
    p.add_argument("--world-turn", type=int, default=0, metavar="TURN",
                   help="World turn for age calculations (default 0).")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress stdout report output.")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    thresholds: dict[str, Any] = {}
    if args.thresholds:
        th_path = Path(args.thresholds)
        if th_path.exists():
            try:
                thresholds = json.loads(th_path.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"Warning: failed to load thresholds: {exc}", file=sys.stderr)

    delta: dict[str, Any] | None = None

    if args.compare:
        before_path = Path(args.compare[0])
        after_path = Path(args.compare[1])
        before_res = analyze_sources([before_path], world_turn=args.world_turn, thresholds=thresholds)
        after_res = analyze_sources([after_path], world_turn=args.world_turn, thresholds=thresholds)
        delta = compute_delta(before_res["fleet"], after_res["fleet"])
        # Use after metrics as primary for the report
        result = after_res
        sources = [before_path, after_path]
    else:
        if not args.sources:
            parser.print_help()
            return 0
        sources = [Path(s) for s in args.sources]
        result = analyze_sources(sources, world_turn=args.world_turn, thresholds=thresholds)

    report_md = render_markdown_report(
        sources,
        result["fleet"],
        result["anomalies"],
        result["violations"],
        delta=delta,
    )

    if not args.quiet:
        print(report_md)

    if args.out:
        out_path = Path(args.out)
        out_path.write_text(report_md, encoding="utf-8")

    if args.json_out:
        jout_path = Path(args.json_out)
        jout_path.write_text(
            json.dumps({
                "fleet": result["fleet"],
                "anomalies": result["anomalies"],
                "violations": result["violations"],
            }, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    return 1 if result["violations"] else 0


if __name__ == "__main__":
    sys.exit(main())
