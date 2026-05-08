"""memory/legacy_bridge.py — Bridge legacy ``agent["memory"]`` to ``memory_v3``.

Called after every ``_add_memory()`` call in tick_rules.py.  Each newly
appended legacy entry is also written as a structured MemoryRecord unless it
is a transient sleep-interval event (``sleep_interval_applied``).

Invariant (section 10):
  Do NOT convert sleep_interval_applied events into long-term MemoryRecords.
  Only final sleep outcomes (sleep_completed, sleep_interrupted) are stored.

Lazy historical import (section 10):
  When memory_v3 is empty, import the last LEGACY_MEMORY_IMPORT_LIMIT legacy
  records via ``import_legacy_memory``.
"""
from __future__ import annotations

import uuid
from typing import Any

from .models import (
    MemoryRecord,
    LAYER_EPISODIC,
    LAYER_THREAT,
    LAYER_WORKING,
    LAYER_SEMANTIC,
    LAYER_SPATIAL,
)
from .store import ensure_memory_v3, add_memory_record, MEMORY_V3_IMPORT_LEGACY_LIMIT

# Action kinds that must never be stored as standalone long-term MemoryRecords.
_SKIP_ACTION_KINDS: frozenset[str] = frozenset({
    "sleep_interval_applied",
})

# ── Mapping tables ─────────────────────────────────────────────────────────────

# legacy memory_type → default layer
_TYPE_TO_LAYER: dict[str, str] = {
    "observation": LAYER_EPISODIC,
    "decision":    LAYER_EPISODIC,
    "action":      LAYER_EPISODIC,
}

# action_kind → (layer, record_kind, tags)
_ACTION_KIND_MAP: dict[str, tuple[str, str, tuple[str, ...]]] = {
    # Sleep outcomes
    "sleep_completed":    (LAYER_EPISODIC, "sleep_completed",    ("sleep", "rest", "recovery")),
    "sleep_interrupted":  (LAYER_EPISODIC, "sleep_interrupted",  ("sleep", "rest", "plan_monitor")),
    "sleep_aborted":      (LAYER_EPISODIC, "sleep_aborted",      ("sleep", "rest", "emergency")),
    # Trade
    "trade_buy":          (LAYER_EPISODIC, "item_bought",         ("trade", "item")),
    "trade_sell":         (LAYER_EPISODIC, "item_sold",           ("trade", "item")),
    "trader_visit":       (LAYER_EPISODIC, "trader_visited",      ("trade", "trader")),
    # Plan monitor
    "plan_monitor_abort": (LAYER_EPISODIC, "action_aborted",      ("plan_monitor", "scheduled_action")),
    # Threat / environment
    "emission_imminent":  (LAYER_THREAT,   "emission_warning",    ("emission", "danger")),
    "emission_started":   (LAYER_THREAT,   "emission_started",    ("emission", "danger")),
    "emission_ended":     (LAYER_EPISODIC, "emission_ended",      ("emission",)),
    "anomaly_detected":   (LAYER_THREAT,   "anomaly_detected",    ("anomaly", "danger")),
    # Combat
    "combat_kill":        (LAYER_THREAT,   "combat_kill",         ("combat", "kill")),
    "combat_killed":      (LAYER_THREAT,   "combat_killed",       ("combat", "death")),
    "combat_wounded":     (LAYER_THREAT,   "combat_wounded",      ("combat", "wounded")),
    "combat_flee":        (LAYER_THREAT,   "combat_flee",         ("combat", "flee")),
    # Exploration
    "explore_confirmed_empty": (LAYER_SPATIAL, "location_empty",  ("exploration", "spatial")),
    "travel_hop":              (LAYER_EPISODIC, "travel_hop",      ("travel",)),
}

# observation type → (layer, kind, tags)
_OBS_TYPE_MAP: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "stalkers":     (LAYER_EPISODIC, "stalkers_seen",    ("stalker", "social")),
    "mutants":      (LAYER_THREAT,   "mutants_seen",     ("mutant", "danger")),
    "items":        (LAYER_SPATIAL,  "items_seen",       ("item", "spatial")),
    "artifacts":    (LAYER_SPATIAL,  "artifact_seen",    ("artifact", "item", "spatial")),
    "combat_kill":  (LAYER_THREAT,   "combat_kill",      ("combat", "kill")),
    "combat_killed":(LAYER_THREAT,   "combat_killed",    ("combat", "death")),
    "combat_wounded":(LAYER_THREAT,  "combat_wounded",   ("combat", "wounded")),
}


def bridge_legacy_entry_to_memory_v3(
    agent: dict[str, Any],
    legacy_entry: dict[str, Any],
    world_turn: int,
) -> None:
    """Convert a single legacy memory entry into a MemoryRecord.

    Called immediately after each ``_add_memory()`` call.
    No-ops for skip-listed transient events.
    """
    effects: dict[str, Any] = legacy_entry.get("effects", {})
    action_kind: str = effects.get("action_kind", "")

    # Skip transient sleep-interval events.
    if action_kind in _SKIP_ACTION_KINDS:
        return

    record = _map_legacy_to_record(agent, legacy_entry, world_turn)
    if record is not None:
        add_memory_record(agent, record)


def import_legacy_memory(
    agent: dict[str, Any],
    agent_id: str,
    world_turn: int,
) -> None:
    """Lazy import: load the last N legacy records into memory_v3 when it is empty.

    Called once per agent when memory_v3 is detected to be empty but legacy
    memory has entries.
    """
    mem_v3 = ensure_memory_v3(agent)
    if mem_v3["records"]:
        return  # Already populated — skip.

    legacy_mem: list[dict[str, Any]] = agent.get("memory", [])
    if not legacy_mem:
        return

    # Import the most recent N entries.
    to_import = legacy_mem[-MEMORY_V3_IMPORT_LEGACY_LIMIT:]
    for entry in to_import:
        record = _map_legacy_to_record(agent, entry, world_turn)
        if record is not None:
            add_memory_record(agent, record)


# ── Mapping logic ──────────────────────────────────────────────────────────────

def _map_legacy_to_record(
    agent: dict[str, Any],
    entry: dict[str, Any],
    world_turn: int,
) -> MemoryRecord | None:
    """Return a MemoryRecord for a legacy entry, or None to skip."""
    effects: dict[str, Any] = entry.get("effects", {})
    action_kind: str = effects.get("action_kind", "")
    memory_type: str = entry.get("type", "observation")
    created_turn: int = int(entry.get("world_turn", world_turn))

    # Skip transient.
    if action_kind in _SKIP_ACTION_KINDS:
        return None

    agent_id: str = agent.get("name", agent.get("agent_id", "unknown"))
    record_id = "mem_leg_" + uuid.uuid4().hex[:10]

    # Determine layer, kind, tags.
    layer, kind, base_tags = _resolve_layer_kind_tags(memory_type, action_kind, effects)

    # Build extra tags from the action_kind (for plan_monitor_abort reasons).
    extra_tags: list[str] = list(base_tags)
    if action_kind == "plan_monitor_abort":
        reason = effects.get("dominant_pressure") or effects.get("reason", "")
        if reason:
            extra_tags.append(str(reason))
        # If this was a sleep abort, also tag sleep.
        scheduled_type = effects.get("scheduled_action_type", "")
        if scheduled_type == "sleep":
            extra_tags += ["sleep", "rest"]
            kind = "sleep_interrupted"

    # Item type tags for trade events.
    item_type_tags: list[str] = []
    item_types_tuple: tuple[str, ...] = ()
    if action_kind in ("trade_buy", "trade_sell"):
        itype = effects.get("item_type") or effects.get("item_category", "")
        if itype:
            extra_tags.append(str(itype))
            item_type_tags.append(str(itype))
            item_types_tuple = (str(itype),)

    all_tags: tuple[str, ...] = tuple(dict.fromkeys(extra_tags))  # preserve order, dedup.

    # Location.
    location_id: str | None = (
        effects.get("location_id")
        or effects.get("to_location")
        or effects.get("destination")
    )

    # Confidence/importance from legacy aggregate fields.
    confidence: float = float(effects.get("confidence", 0.7))
    importance_tier: str = effects.get("importance", "")
    if importance_tier == "critical":
        importance: float = 1.0
    elif importance_tier == "tactical":
        importance = 0.7
    else:
        importance = 0.5

    # Merge legacy semantics (first_seen, last_seen, times_seen) into details.
    details: dict[str, Any] = dict(effects)
    # Clean up large redundant sub-keys if any (keep details lean).

    # Sleep-specific detail extraction.
    if action_kind in ("sleep_completed", "sleep_interrupted"):
        details = {
            k: v for k, v in effects.items()
            if k in (
                "sleep_intervals_applied",
                "turns_total",
                "turns_slept",
                "hours_slept",
                "wake_due_to_rested",
                "sleepiness_after",
                "sleep_progress_turns",
                "dominant_pressure",
                "scheduled_action_type",
                "reason",
                "first_seen_turn",
                "last_seen_turn",
                "times_seen",
            )
        }
        if action_kind == "sleep_interrupted":
            importance = 0.7  # medium-high retention

    summary: str = entry.get("summary", entry.get("title", f"{kind} at {location_id or 'unknown'}"))

    return MemoryRecord(
        id=record_id,
        agent_id=agent_id,
        layer=layer,
        kind=kind,
        created_turn=created_turn,
        last_accessed_turn=None,
        summary=summary,
        details=details,
        location_id=location_id,
        item_types=item_types_tuple,
        tags=all_tags,
        importance=importance,
        confidence=confidence,
        source="legacy_import",
    )


def _resolve_layer_kind_tags(
    memory_type: str,
    action_kind: str,
    effects: dict[str, Any],
) -> tuple[str, str, tuple[str, ...]]:
    """Return (layer, kind, base_tags) for a legacy entry."""
    # Priority 1: action_kind mapping.
    if action_kind and action_kind in _ACTION_KIND_MAP:
        layer, kind, tags = _ACTION_KIND_MAP[action_kind]
        return layer, kind, tags

    # Priority 2: observation type mapping.
    obs_type = effects.get("observed", "")
    if obs_type and obs_type in _OBS_TYPE_MAP:
        layer, kind, tags = _OBS_TYPE_MAP[obs_type]
        return layer, kind, tags

    # Fallback: use memory_type to determine layer.
    layer = _TYPE_TO_LAYER.get(memory_type, LAYER_EPISODIC)
    kind = action_kind or obs_type or memory_type
    return layer, kind, ()
