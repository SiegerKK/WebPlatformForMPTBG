#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _iter_exports(folder: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    full_debug: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.json")):
        payload = _load_json(path)
        if not isinstance(payload, dict):
            continue
        name = path.name.lower()
        if "full_debug" in name:
            full_debug.append(payload)
        elif "history" in name:
            history.append(payload)
    return full_debug, history


def _memory_records(agent: dict[str, Any]) -> list[dict[str, Any]]:
    memory_v3 = agent.get("memory_v3")
    records = memory_v3.get("records") if isinstance(memory_v3, dict) else {}
    if not isinstance(records, dict):
        return []
    return [raw for raw in records.values() if isinstance(raw, dict)]


def analyze(folder: Path) -> int:
    full_debug, history = _iter_exports(folder)
    if not full_debug and not history:
        print("No *_full_debug*.json or *_history*.json files found.")
        return 1

    layer_counter: Counter[str] = Counter()
    kind_counter: Counter[str] = Counter()
    at_cap = 0
    left_zone_active = 0
    dead_hp_positive = 0
    story_gap = 0

    history_story_by_agent: dict[str, int] = {}
    for item in history:
        agent = item.get("agent") if isinstance(item.get("agent"), dict) else {}
        aid = str(agent.get("id") or "")
        story = item.get("story_events")
        history_story_by_agent[aid] = len(story) if isinstance(story, list) else 0

    print("Per-agent summary")
    print("-" * 96)
    print(f"{'agent':<24} {'records':>7} {'stalkers_seen':>13} {'semantic%':>10} {'at_cap':>7} {'story':>7}")
    print("-" * 96)
    for payload in full_debug:
        aid = str(payload.get("id") or payload.get("agent_id") or payload.get("name") or "unknown")
        records = _memory_records(payload)
        records_count = len(records)
        by_layer = Counter(str(r.get("layer") or "") for r in records)
        by_kind = Counter(str(r.get("kind") or "") for r in records)
        semantic_ratio = (by_layer.get("semantic", 0) / records_count) if records_count else 0.0
        stalkers_seen_count = by_kind.get("stalkers_seen", 0)
        is_cap = records_count >= 500
        story_events = payload.get("story_events")
        story_count = len(story_events) if isinstance(story_events, list) else 0

        layer_counter.update(by_layer)
        kind_counter.update(by_kind)
        if is_cap:
            at_cap += 1
        if payload.get("has_left_zone") and payload.get("current_goal") == "restore_needs":
            left_zone_active += 1
        if payload.get("is_alive") is False and int(payload.get("hp", 0) or 0) > 0:
            dead_hp_positive += 1
        if history_story_by_agent.get(aid, 0) > 0 and story_count == 0:
            story_gap += 1

        print(
            f"{aid:<24} {records_count:>7} {stalkers_seen_count:>13} "
            f"{semantic_ratio * 100:>9.1f}% {str(is_cap):>7} {story_count:>7}"
        )

    print("\nAggregate memory layers")
    for layer, count in layer_counter.most_common():
        print(f"  {layer or 'unknown'}: {count}")

    print("\nTop memory kinds")
    for kind, count in kind_counter.most_common(15):
        print(f"  {kind or 'unknown'}: {count}")

    print("\nAnomalies")
    print(f"  agents_at_cap: {at_cap}")
    print(f"  left_zone_but_active_restore_goal: {left_zone_active}")
    print(f"  dead_but_hp_positive: {dead_hp_positive}")
    print(f"  full_debug_history_story_gap: {story_gap}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze Zone Stalkers full_debug/history NPC exports.")
    parser.add_argument("folder", type=Path, help="Folder with *_full_debug*.json and *_history*.json files.")
    args = parser.parse_args()
    return analyze(args.folder)


if __name__ == "__main__":
    raise SystemExit(main())
