"""
memory_merge.py — Observation merge/aggregation helpers for NPC memory.

Implements a three-tier classification system for Zone Stalkers agent memory:

  CRITICAL  — never merged  (kill, wound, flee, artifact-found, intel-bought …)
  TACTICAL  — careful merge, window = 20 turns (agent seen, group seen, trader)
  AMBIENT   — strong aggregation, window = 40 turns (items, route passages, wait)

Merge is keyed by a *semantic signature*:
    (obs_type, subject_id, location_id, semantic_status)

Two entries with the same signature within the merge window are collapsed into
one, with ``times_seen``, ``last_seen_turn``, and ``confidence`` updated.

Confidence formula:
    confidence = min(1.0, base_confidence + log(times_seen) * k)   k = 0.1

Staleness thresholds (turns after ``last_seen_turn``):
    CRITICAL  → 9 999  (effectively never stale)
    TACTICAL  → 45
    AMBIENT   → 30

Stale items are NOT deleted; their ``status`` is set to ``"stale"`` and
``confidence`` is decayed.  Readers that care about freshness should check
``effects["status"] != "stale"``.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

# ── Tier labels ──────────────────────────────────────────────────────────────
CRITICAL = "critical"
TACTICAL = "tactical"
AMBIENT  = "ambient"

# ── Merge windows (turns) ────────────────────────────────────────────────────
MERGE_WINDOW: Dict[str, int] = {
    CRITICAL: 0,   # never merged
    TACTICAL: 20,
    AMBIENT:  40,
}

# ── Staleness thresholds (turns since last_seen_turn) ────────────────────────
STALE_AFTER: Dict[str, int] = {
    CRITICAL: 9_999,
    TACTICAL: 45,
    AMBIENT:  30,
}

# Base confidence for a brand-new observation.
_BASE_CONFIDENCE: float = 0.7
# Log multiplier for confidence growth with repeated sightings.
_CONFIDENCE_K: float = 0.1

# ── Classification maps ──────────────────────────────────────────────────────
# effects["observed"] → importance tier
_OBS_TYPE_IMPORTANCE: Dict[str, str] = {
    "stalkers":         TACTICAL,
    "mutants":          TACTICAL,
    "items":            AMBIENT,
    "artifacts":        AMBIENT,
    "combat_kill":      CRITICAL,
    "combat_killed":    CRITICAL,
    "combat_wounded":   CRITICAL,
}

# effects["action_kind"] → importance tier (takes precedence when both are set)
_ACTION_KIND_IMPORTANCE: Dict[str, str] = {
    "retreat_observed":       CRITICAL,
    "hunt_target_killed":     CRITICAL,
    "combat_flee":            CRITICAL,
    "intel_from_trader":      TACTICAL,
    "intel_from_stalker":     TACTICAL,
    "explore_confirmed_empty": AMBIENT,
    "travel_hop":             AMBIENT,
    "wait_in_shelter":        AMBIENT,
    "emission_ended":         TACTICAL,
    "emission_warning":       TACTICAL,
}


# ── Public API ───────────────────────────────────────────────────────────────

def get_importance(effects: Dict[str, Any]) -> str:
    """Return the importance tier for an observation *effects* dict."""
    ak = effects.get("action_kind")
    if ak:
        tier = _ACTION_KIND_IMPORTANCE.get(ak)
        if tier:
            return tier
    obs = effects.get("observed")
    if obs:
        tier = _OBS_TYPE_IMPORTANCE.get(obs)
        if tier:
            return tier
    return AMBIENT  # safe default


def is_critical_observation(effects: Dict[str, Any]) -> bool:
    """Return ``True`` if this observation must never be merged."""
    return get_importance(effects) == CRITICAL


def merge_signature(effects: Dict[str, Any]) -> Optional[Tuple]:
    """Return a hashable merge key for *effects*, or ``None`` for critical/unmergeable.

    Signature layout: ``(obs_type, subject_id, location_id, semantic_status)``

    * **stalkers** — track group presence; names are unioned over time.
      Semantic status is always ``"present"`` so any co-location sightings merge.
    * **mutants** — merge only when the exact same group is visible.
      Semantic status encodes the sorted names tuple so a different pack breaks
      aggregation.
    * **items** — track what is currently on the ground.
      Semantic status is ``"ground"``; item_types are replaced (not unioned) on
      each update.
    """
    if is_critical_observation(effects):
        return None

    obs_type = effects.get("observed")
    loc_id   = effects.get("location_id")

    if obs_type == "stalkers":
        return ("stalkers", None, loc_id, "present")
    if obs_type == "mutants":
        names = tuple(sorted(effects.get("names", [])))
        return ("mutants", None, loc_id, names)
    if obs_type == "items":
        return ("items", None, loc_id, "ground")
    if obs_type == "artifacts":
        return ("artifacts", None, loc_id, "present")

    return None  # unknown obs_type — don't attempt merge


def find_mergeable_entry(
    memory: list,
    effects: Dict[str, Any],
    current_turn: int,
) -> Optional[Dict[str, Any]]:
    """Return the most recent memory entry that can be merged with *effects*.

    Merge conditions (all must hold):
    1. Same merge signature (type + location + semantic status).
    2. ``current_turn - last_seen_turn <= MERGE_WINDOW[importance]``.
    3. Not a critical observation.

    Returns the **mutable** entry dict so the caller can update it in-place, or
    ``None`` when a fresh entry should be appended instead.

    The search scans memory from newest to oldest and stops at the first entry
    whose ``last_seen_turn`` is outside the merge window (memory is append-only
    so it is always ordered by time).
    """
    sig = merge_signature(effects)
    if sig is None:
        return None

    importance = get_importance(effects)
    window = MERGE_WINDOW[importance]

    for entry in reversed(memory):
        if entry.get("type") != "observation":
            continue
        fx = entry.get("effects", {})
        existing_sig = merge_signature(fx)
        if existing_sig is None:
            continue

        # Compute how old the existing entry is.
        last_seen = fx.get("last_seen_turn", entry.get("world_turn", 0))
        age = current_turn - last_seen

        if age > window:
            # All earlier entries are even older — stop scanning.
            break

        if existing_sig == sig:
            return entry

    return None


def update_merged_entry(entry: Dict[str, Any], world_turn: int) -> None:
    """Update *entry* in-place to reflect one more sighting at *world_turn*.

    Increments ``times_seen``, refreshes ``last_seen_turn``, recalculates
    ``confidence``, and resets ``status`` to ``"active"``.

    Handles legacy entries that were written before the merge system existed by
    initialising the new fields from the entry's ``world_turn``.
    """
    fx = entry.setdefault("effects", {})

    # Back-fill legacy entries that lack the new fields.
    if "first_seen_turn" not in fx:
        fx["first_seen_turn"] = entry.get("world_turn", world_turn)
    if "times_seen" not in fx:
        fx["times_seen"] = 1
    if "confidence" not in fx:
        fx["confidence"] = _BASE_CONFIDENCE
    if "importance" not in fx:
        fx["importance"] = get_importance(fx)

    fx["times_seen"]    = fx["times_seen"] + 1
    fx["last_seen_turn"] = world_turn
    fx["status"]        = "active"

    # Confidence formula: min(1.0, base + log(times_seen) * k)
    times = fx["times_seen"]
    fx["confidence"] = min(1.0, _BASE_CONFIDENCE + math.log(times) * _CONFIDENCE_K)

    # NOTE: entry["world_turn"] is intentionally NOT updated here.
    # It represents the last *semantic* change (content changed), while
    # effects["last_seen_turn"] tracks the most recent raw sighting.
    # Callers that update content (names, item_types) should set
    # entry["world_turn"] = world_turn themselves after calling this function.


def new_obs_aggregate_fields(effects: Dict[str, Any], world_turn: int) -> Dict[str, Any]:
    """Return the aggregate fields to embed in a *new* (first-ever) observation.

    The returned dict should be spread into the ``effects`` payload *before*
    calling ``_add_memory`` so that all new entries carry the full schema.
    Callers that already set any of these keys will see their values preserved
    (this function only fills in missing keys).
    """
    importance = get_importance(effects)
    return {
        "first_seen_turn": world_turn,
        "last_seen_turn":  world_turn,
        "times_seen":      1,
        "confidence":      _BASE_CONFIDENCE,
        "importance":      importance,
        "status":          "active",
    }


def apply_staleness(memory: list, current_turn: int) -> None:
    """Mark observations as ``"stale"`` when they are past their freshness window.

    Only entries that already carry the new aggregate fields (``last_seen_turn``)
    are examined; legacy entries without these fields are left untouched.

    Stale entries are **not deleted**.  Their ``confidence`` is decayed and
    ``status`` is set to ``"stale"``.  The decay rate is 0.01 per turn past the
    staleness threshold, capped at 0.5 total reduction.
    """
    for entry in memory:
        if entry.get("type") != "observation":
            continue
        fx = entry.get("effects", {})
        if fx.get("status") in ("stale", "archived"):
            continue

        last_seen = fx.get("last_seen_turn")
        if last_seen is None:
            continue  # old-format entry — skip

        importance  = fx.get("importance") or get_importance(fx)
        stale_after = STALE_AFTER.get(importance, STALE_AFTER[AMBIENT])

        if current_turn - last_seen > stale_after:
            fx["status"] = "stale"
            age_past_threshold = current_turn - last_seen - stale_after
            decay = min(0.5, age_past_threshold * 0.01)
            fx["confidence"] = max(0.0, fx.get("confidence", _BASE_CONFIDENCE) - decay)
