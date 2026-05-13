"""memory/cold_store.py — Cold memory store for agent memory_v3 + knowledge_v1.

Keeps heavy ``memory_v3`` records/indexes and ``knowledge_v1`` tables out of
the hot agent state blob that is serialised on every tick (Redis + DB).

Hot agent state after migration contains only:
    memory_ref:     str key → "ctx:agent_memory:<context_id>:<agent_id>"
    memory_summary: lightweight counters (records_count, revisions, dirty flag)

Cold memory is loaded **on demand** (brain decision, memory write, debug export)
and saved back to the cold store when dirty.

Storage backends
----------------
Redis   — used when ``redis_client`` is provided (production / integration).
In-mem  — ``_IN_MEMORY_STORE`` dict; used in unit tests / local dev with no
          Redis.  The dict is process-scoped and keyed by memory_ref string.

Migration path
--------------
On first tick for an agent that has ``memory_v3`` but no ``memory_ref``:
  1. Snapshot ``memory_v3`` and ``knowledge_v1`` into a cold blob.
  2. Save blob to cold store.
  3. Add ``memory_ref`` + ``memory_summary`` to hot agent state.
  4. Remove ``memory_v3`` from hot agent state.

Backward compat
---------------
If cold store is unavailable and agent still has a legacy ``memory_v3`` in hot
state, all memory helpers fall back to the legacy in-process path unchanged.

Metrics
-------
Cold store counters are stored in a per-process dict for debugging:
    cold_memory_loads, cold_memory_saves, cold_memory_load_ms,
    cold_memory_save_ms, cold_memory_bytes.
"""
from __future__ import annotations

import json
import time
from typing import Any

# ── Process-level in-memory fallback (no Redis required for tests/local) ─────
# Maps memory_ref string → serialised JSON bytes (bytes to match Redis interface).
_IN_MEMORY_STORE: dict[str, bytes] = {}

# ── Global process-level metrics ─────────────────────────────────────────────
_COLD_METRICS: dict[str, int | float] = {
    "cold_memory_loads": 0,
    "cold_memory_saves": 0,
    "cold_memory_load_ms": 0.0,
    "cold_memory_save_ms": 0.0,
    "cold_memory_bytes": 0,
}

# Schema version stored in every cold blob.
_COLD_BLOB_VERSION = 1

# TTL for Redis cold memory keys (30 days); cold memory should outlive the state
# cache TTL to prevent data loss on state reloads.
_COLD_TTL_SECONDS = 60 * 60 * 24 * 30


# ── Public helpers ────────────────────────────────────────────────────────────

def get_cold_metrics() -> dict[str, int | float]:
    """Return a snapshot of cold-store operation counters."""
    return dict(_COLD_METRICS)


def reset_cold_metrics() -> None:
    """Reset cold-store metrics to zero (useful in tests)."""
    for key in _COLD_METRICS:
        _COLD_METRICS[key] = 0  # type: ignore[assignment]


def clear_in_memory_store() -> None:
    """Clear the in-memory cold store (for test isolation)."""
    _IN_MEMORY_STORE.clear()


# ── Key helpers ───────────────────────────────────────────────────────────────

def get_agent_memory_ref(context_id: str, agent_id: str) -> str:
    """Return the canonical Redis/in-memory key for an agent's cold memory."""
    return f"ctx:agent_memory:{context_id}:{agent_id}"


# ── Blob construction ─────────────────────────────────────────────────────────

def _build_empty_memory_blob(agent_id: str) -> dict[str, Any]:
    return {
        "version": _COLD_BLOB_VERSION,
        "agent_id": agent_id,
        "memory_v3": {
            "schema_version": 1,
            "records": {},
            "indexes": {
                "by_layer": {},
                "by_kind": {},
                "by_location": {},
                "by_entity": {},
                "by_item_type": {},
                "by_tag": {},
            },
            "stats": {
                "records_count": 0,
                "last_decay_turn": None,
                "last_consolidation_turn": None,
                "memory_revision": 0,
                "memory_evictions": 0,
                "dropped_new_records": 0,
                "memory_write_attempts": 0,
                "memory_write_dropped": 0,
                "memory_index_rebuilds": 0,
            },
        },
        "knowledge_v1": {
            "revision": 0,
            "known_npcs": {},
            "known_locations": {},
            "known_traders": {},
            "known_hazards": {},
            "stats": {
                "known_npcs_count": 0,
                "detailed_known_npcs_count": 0,
                "last_update_turn": 0,
            },
        },
    }


def _blob_from_agent(agent_id: str, agent: dict[str, Any]) -> dict[str, Any]:
    """Build a cold blob from the agent's current hot state."""
    memory_v3 = agent.get("memory_v3")
    if not isinstance(memory_v3, dict):
        memory_v3 = _build_empty_memory_blob(agent_id)["memory_v3"]

    knowledge_v1 = agent.get("knowledge_v1")
    if not isinstance(knowledge_v1, dict):
        knowledge_v1 = _build_empty_memory_blob(agent_id)["knowledge_v1"]

    return {
        "version": _COLD_BLOB_VERSION,
        "agent_id": agent_id,
        "memory_v3": memory_v3,
        "knowledge_v1": knowledge_v1,
    }


def build_memory_summary(
    agent_id: str,
    agent: dict[str, Any],
    *,
    is_loaded: bool = False,
    dirty: bool = False,
) -> dict[str, Any]:
    """Build a ``memory_summary`` dict from the agent's current hot state."""
    memory_v3 = agent.get("memory_v3")
    records_count = 0
    memory_revision = 0
    last_memory_write_turn = None
    last_compaction_turn = None

    if isinstance(memory_v3, dict):
        stats = memory_v3.get("stats")
        if isinstance(stats, dict):
            records_count = int(stats.get("records_count", 0) or 0)
            memory_revision = int(stats.get("memory_revision", 0) or 0)
            last_compaction_turn = stats.get("last_consolidation_turn")
        records = memory_v3.get("records")
        if isinstance(records, dict) and records:
            turns = [
                int(r.get("created_turn", 0) or 0)
                for r in records.values()
                if isinstance(r, dict)
            ]
            last_memory_write_turn = max(turns) if turns else None

    knowledge_v1 = agent.get("knowledge_v1")
    knowledge_revision = 0
    if isinstance(knowledge_v1, dict):
        knowledge_revision = int(knowledge_v1.get("revision", 0) or 0)

    # Preserve existing summary fields if they exist (for incremental updates).
    existing_summary = agent.get("memory_summary")
    if isinstance(existing_summary, dict):
        return {
            "records_count": records_count,
            "memory_revision": memory_revision,
            "knowledge_revision": knowledge_revision,
            "last_memory_write_turn": last_memory_write_turn,
            "last_compaction_turn": last_compaction_turn,
            "cold_store_version": _COLD_BLOB_VERSION,
            "is_loaded": is_loaded,
            "dirty": dirty,
        }
    return {
        "records_count": records_count,
        "memory_revision": memory_revision,
        "knowledge_revision": knowledge_revision,
        "last_memory_write_turn": last_memory_write_turn,
        "last_compaction_turn": last_compaction_turn,
        "cold_store_version": _COLD_BLOB_VERSION,
        "is_loaded": is_loaded,
        "dirty": dirty,
    }


# ── Storage helpers ───────────────────────────────────────────────────────────

def _serialise_blob(blob: dict[str, Any]) -> bytes:
    return json.dumps(blob, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _deserialise_blob(raw: bytes) -> dict[str, Any] | None:
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def _redis_get(redis_client: Any, key: str) -> bytes | None:
    try:
        return redis_client.get(key)
    except Exception:
        return None


def _redis_set(redis_client: Any, key: str, value: bytes) -> None:
    try:
        redis_client.set(key, value, ex=_COLD_TTL_SECONDS)
    except Exception:
        pass


def _store_get(key: str, redis_client: Any | None) -> bytes | None:
    if redis_client is not None:
        raw = _redis_get(redis_client, key)
        if raw is not None:
            return raw
    return _IN_MEMORY_STORE.get(key)


def _store_set(key: str, value: bytes, redis_client: Any | None) -> None:
    if redis_client is not None:
        _redis_set(redis_client, key, value)
    else:
        _IN_MEMORY_STORE[key] = value


# ── Public API ────────────────────────────────────────────────────────────────

def mark_agent_memory_dirty(agent: dict[str, Any]) -> None:
    """Mark the agent's cold memory as needing a save at end of tick."""
    summary = agent.get("memory_summary")
    if isinstance(summary, dict):
        summary["dirty"] = True
    else:
        agent["memory_summary"] = {
            "records_count": 0,
            "memory_revision": 0,
            "knowledge_revision": 0,
            "last_memory_write_turn": None,
            "last_compaction_turn": None,
            "cold_store_version": _COLD_BLOB_VERSION,
            "is_loaded": True,
            "dirty": True,
        }


def _update_summary_from_loaded_data(agent: dict[str, Any]) -> None:
    """Refresh counters in memory_summary from the currently-loaded memory."""
    summary = agent.get("memory_summary")
    if not isinstance(summary, dict):
        return

    memory_v3 = agent.get("memory_v3")
    if isinstance(memory_v3, dict):
        stats = memory_v3.get("stats") or {}
        summary["records_count"] = int(stats.get("records_count", 0) or 0)
        summary["memory_revision"] = int(stats.get("memory_revision", 0) or 0)
        summary["last_compaction_turn"] = stats.get("last_consolidation_turn")

        records = memory_v3.get("records") or {}
        if isinstance(records, dict) and records:
            turns = [
                int(r.get("created_turn", 0) or 0)
                for r in records.values()
                if isinstance(r, dict)
            ]
            if turns:
                summary["last_memory_write_turn"] = max(turns)

    knowledge_v1 = agent.get("knowledge_v1")
    if isinstance(knowledge_v1, dict):
        summary["knowledge_revision"] = int(knowledge_v1.get("revision", 0) or 0)

    summary["is_loaded"] = True


def load_agent_memory(
    *,
    context_id: str,
    agent_id: str,
    agent: dict[str, Any],
    redis_client: Any | None = None,
) -> dict[str, Any]:
    """Load cold memory from the store into the agent's hot state.

    Places ``memory_v3`` and ``knowledge_v1`` back into *agent* so that all
    existing memory helpers can work unchanged.

    Returns the cold blob dict (for callers that need to inspect or mutate it
    directly).  If the cold blob is not found, an empty blob is created and
    stored.
    """
    t0 = time.perf_counter()

    ref = get_agent_memory_ref(context_id, agent_id)
    raw = _store_get(ref, redis_client)

    if raw is not None:
        blob = _deserialise_blob(raw)
        if not isinstance(blob, dict):
            blob = None
    else:
        blob = None

    if blob is None:
        # No cold blob yet — build from current hot state (if any) or empty.
        blob = _blob_from_agent(agent_id, agent)
        encoded = _serialise_blob(blob)
        _store_set(ref, encoded, redis_client)
        _COLD_METRICS["cold_memory_bytes"] = int(
            _COLD_METRICS["cold_memory_bytes"]  # type: ignore[operator]
        ) + len(encoded)

    # ── Restore hot state ─────────────────────────────────────────────────────
    memory_v3 = blob.get("memory_v3")
    if isinstance(memory_v3, dict):
        agent["memory_v3"] = memory_v3

    knowledge_v1 = blob.get("knowledge_v1")
    if isinstance(knowledge_v1, dict):
        agent["knowledge_v1"] = knowledge_v1

    _update_summary_from_loaded_data(agent)

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    _COLD_METRICS["cold_memory_loads"] = int(_COLD_METRICS["cold_memory_loads"]) + 1  # type: ignore[operator]
    _COLD_METRICS["cold_memory_load_ms"] = float(_COLD_METRICS["cold_memory_load_ms"]) + elapsed_ms  # type: ignore[operator]

    return blob


def save_agent_memory_if_dirty(
    *,
    context_id: str,
    agent_id: str,
    agent: dict[str, Any],
    redis_client: Any | None = None,
) -> bool:
    """Persist cold memory to the store if the agent's memory_summary is dirty.

    Returns ``True`` when a save actually happened.
    """
    summary = agent.get("memory_summary")
    if not isinstance(summary, dict):
        return False
    if not summary.get("dirty"):
        return False

    t0 = time.perf_counter()

    # Build the cold blob from current (loaded) hot state.
    blob = _blob_from_agent(agent_id, agent)
    encoded = _serialise_blob(blob)
    ref = get_agent_memory_ref(context_id, agent_id)
    _store_set(ref, encoded, redis_client)

    summary["dirty"] = False

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    _COLD_METRICS["cold_memory_saves"] = int(_COLD_METRICS["cold_memory_saves"]) + 1  # type: ignore[operator]
    _COLD_METRICS["cold_memory_save_ms"] = float(_COLD_METRICS["cold_memory_save_ms"]) + elapsed_ms  # type: ignore[operator]
    _COLD_METRICS["cold_memory_bytes"] = int(_COLD_METRICS["cold_memory_bytes"]) + len(encoded)  # type: ignore[operator]

    return True


def ensure_agent_memory_loaded(
    *,
    context_id: str,
    agent_id: str,
    agent: dict[str, Any],
    redis_client: Any | None = None,
) -> dict[str, Any]:
    """Ensure cold memory is loaded into the agent's hot state.

    If already loaded (``memory_summary["is_loaded"]``), this is a no-op.
    Otherwise delegates to :func:`load_agent_memory`.

    Returns the cold blob dict.
    """
    summary = agent.get("memory_summary")
    if isinstance(summary, dict) and summary.get("is_loaded"):
        # Already loaded for this tick — return quickly.
        blob = _blob_from_agent(agent_id, agent)
        return blob

    return load_agent_memory(
        context_id=context_id,
        agent_id=agent_id,
        agent=agent,
        redis_client=redis_client,
    )


def migrate_agent_memory_to_cold_store(
    *,
    context_id: str,
    agent_id: str,
    agent: dict[str, Any],
    redis_client: Any | None = None,
) -> None:
    """Migrate a legacy agent from hot ``memory_v3`` to cold store.

    On completion:
    * Cold blob is saved to the store.
    * ``agent["memory_ref"]`` and ``agent["memory_summary"]`` are set.
    * ``agent["memory_v3"]`` is removed from the hot state (it has moved cold).
      ``knowledge_v1`` stays in hot state during the current tick so that all
      existing code paths that read it continue to work; it will be stripped
      at the end of the tick in :func:`strip_cold_memory_from_hot_state`.
    """
    if agent.get("memory_ref"):
        # Already migrated — nothing to do.
        return

    ref = get_agent_memory_ref(context_id, agent_id)

    blob = _blob_from_agent(agent_id, agent)
    encoded = _serialise_blob(blob)
    _store_set(ref, encoded, redis_client)

    agent["memory_ref"] = ref
    summary = build_memory_summary(
        agent_id, agent, is_loaded=True, dirty=False
    )
    agent["memory_summary"] = summary

    # Strip memory_v3 from hot state — it now lives in the cold store.
    agent.pop("memory_v3", None)

    _COLD_METRICS["cold_memory_saves"] = int(_COLD_METRICS["cold_memory_saves"]) + 1  # type: ignore[operator]
    _COLD_METRICS["cold_memory_bytes"] = int(_COLD_METRICS["cold_memory_bytes"]) + len(encoded)  # type: ignore[operator]


def strip_cold_memory_from_hot_state(agent: dict[str, Any]) -> None:
    """Remove transiently-loaded memory fields from hot agent state.

    Called at end of tick after :func:`save_agent_memory_if_dirty` to keep the
    hot state blob small.  Only strips when the agent has a ``memory_ref``
    (i.e. has been migrated to the cold store).

    Fields removed:
    * ``memory_v3`` — moved to cold store during migration.
    * ``knowledge_v1`` — kept in cold blob; removed from hot state after save.
    """
    if not agent.get("memory_ref"):
        return
    agent.pop("memory_v3", None)
    agent.pop("knowledge_v1", None)
    summary = agent.get("memory_summary")
    if isinstance(summary, dict):
        summary["is_loaded"] = False


def flush_dirty_agent_memories(
    *,
    context_id: str,
    state: dict[str, Any],
    redis_client: Any | None = None,
) -> int:
    """Save dirty cold memories for all agents and strip loaded data from hot state.

    Called once at end of every tick.  Returns the number of agents saved.
    """
    agents = state.get("agents") or {}
    saved = 0
    for agent_id, agent in agents.items():
        if not isinstance(agent, dict):
            continue
        if not agent.get("memory_ref"):
            continue
        save_agent_memory_if_dirty(
            context_id=context_id,
            agent_id=agent_id,
            agent=agent,
            redis_client=redis_client,
        )
        strip_cold_memory_from_hot_state(agent)
        saved += 1
    return saved


def get_context_id_from_state(state: dict[str, Any]) -> str:
    """Extract context_id from the state dict.

    State may not always carry context_id (e.g. in tests or legacy invocations).
    Falls back to ``"default"`` which is safe for the in-memory store.
    """
    ctx = state.get("context_id") or state.get("_context_id")
    if ctx:
        return str(ctx)
    return "default"
