"""context_builder — build AgentContext from raw world state.

``build_agent_context`` is called **once per tick per agent** before any
decision logic runs.  It does not make gameplay decisions.

This function may update the following **agent runtime-only** diagnostic and
cache fields — these are intentional mutations, not gameplay side-effects:
    - ``agent["brain_context_cache"]``   — derived context cache payload
    - ``agent["brain_context_metrics"]`` — per-agent call/hit/miss counters

Visibility model (Phase 1 MVP):
    An agent "sees":
    1. All other agents co-located at the same location.
    2. All objects (items, traders) at the same location.
    3. Agents / locations referenced in the agent's recent memory.
    4. Intel transferred in dialogue (Phase 6+).
    5. Signals from the group (Phase 7+).
"""
from __future__ import annotations

import copy
import time
from typing import Any

from app.games.zone_stalkers.decision.constants import CRITICAL_REST_THRESHOLD
from app.games.zone_stalkers.rules.tick_constants import (
    CRITICAL_HP_THRESHOLD,
    CRITICAL_HUNGER_THRESHOLD,
    CRITICAL_THIRST_THRESHOLD,
)

from .models.agent_context import AgentContext

CONTEXT_CACHE_TURN_BUCKET_SIZE = 10


def build_agent_context(
    agent_id: str,
    agent: dict[str, Any],
    state: dict[str, Any],
    *,
    force_refresh: bool = False,
    deep_debug: bool = False,
) -> AgentContext:
    """Return a normalised ``AgentContext`` for the given agent.

    Parameters
    ----------
    agent_id
        The stable key of the agent in ``state["agents"]``.
    agent
        The agent dict (``state["agents"][agent_id]``).
    state
        The full world state dict (read-only semantics for game data).
    force_refresh
        When ``True``, bypass the context cache and rebuild from scratch.
    deep_debug
        When ``True``, bypass the context cache for full diagnostic accuracy.

    Returns
    -------
    AgentContext
        Populated snapshot; never ``None``.

    Notes
    -----
    This function may write to ``agent["brain_context_cache"]`` and
    ``agent["brain_context_metrics"]`` as runtime-only diagnostic fields.
    These are not gameplay decisions and do not affect game state semantics.
    """
    started = time.perf_counter()
    metrics = _ensure_context_builder_metrics(agent)
    metrics["context_builder_calls"] = int(metrics.get("context_builder_calls", 0)) + 1

    locations: dict[str, Any] = state.get("locations", {})
    agents: dict[str, Any] = state.get("agents", {})
    traders: dict[str, Any] = state.get("traders", {})
    loc_id: str = agent.get("location_id", "")
    loc: dict[str, Any] = locations.get(loc_id, {})

    # ── World context ─────────────────────────────────────────────────────────
    world_context: dict[str, Any] = {
        "world_turn": state.get("world_turn", 0),
        "world_day": state.get("world_day", 1),
        "world_hour": state.get("world_hour", 6),
        "world_minute": state.get("world_minute", 0),
        "emission_active": state.get("emission_active", False),
        "emission_scheduled_turn": state.get("emission_scheduled_turn"),
        "emission_ends_turn": state.get("emission_ends_turn"),
    }

    # ── Visible entities (co-located agents + traders) ────────────────────────
    visible_entities: list[dict[str, Any]] = []
    for other_id, other in agents.items():
        if other_id == agent_id:
            continue
        if not other.get("is_alive", True):
            continue
        if other.get("has_left_zone"):
            continue
        if other.get("location_id") == loc_id:
            visible_entities.append({
                "agent_id": other_id,
                "name": other.get("name", other_id),
                "archetype": other.get("archetype"),
                "is_trader": other.get("archetype") == "trader_agent",
                "global_goal": other.get("global_goal"),
                "hp": other.get("hp", 100),
                "is_alive": other.get("is_alive", True),
            })

    # P3 fix: also add traders from state["traders"] when co-located
    for trader_id, trader in traders.items():
        if not trader.get("is_alive", True):
            continue
        if trader.get("location_id") == loc_id:
            visible_entities.append({
                "agent_id": trader_id,
                "name": trader.get("name", trader_id),
                "archetype": "trader_agent",
                "is_trader": True,
                "global_goal": None,
                "hp": trader.get("hp", 100),
                "is_alive": True,
            })

    # ── Combat context ────────────────────────────────────────────────────────
    combat_context: dict[str, Any] | None = _combat_context_for(agent_id, state)

    world_turn: int = world_context["world_turn"]
    target_id: str | None = str(agent.get("kill_target_id") or "") or None
    objective_key = _resolve_objective_key(agent)
    cache_key = _build_context_cache_key(
        agent=agent,
        state=state,
        world_turn=world_turn,
        location_id=loc_id,
        objective_key=objective_key,
        target_id=target_id,
    )
    bypass_cache = _should_bypass_context_cache(
        agent=agent,
        state=state,
        combat_context=combat_context,
        force_refresh=force_refresh,
        deep_debug=deep_debug,
    )

    scan_metrics: dict[str, int] = {
        "memory_scan_records": 0,
        "knowledge_entries_scanned": 0,
    }
    if not bypass_cache:
        cached_parts = _get_context_cache(agent, cache_key, world_turn=world_turn)
    else:
        cached_parts = None

    if cached_parts is not None:
        metrics["context_builder_cache_hits"] = int(metrics.get("context_builder_cache_hits", 0)) + 1
        derived_parts = cached_parts
    else:
        metrics["context_builder_cache_misses"] = int(metrics.get("context_builder_cache_misses", 0)) + 1
        if agent.get("memory_ref") and (
            not isinstance(agent.get("memory_v3"), dict) or not isinstance(agent.get("knowledge_v1"), dict)
        ):
            try:
                from app.games.zone_stalkers.memory.cold_store import (  # noqa: PLC0415
                    ensure_agent_memory_loaded as _ensure_cold_loaded_for_context,
                    get_zone_cold_memory_redis_client as _resolve_cold_redis_client,
                    record_agent_cold_memory_error as _record_cold_error,
                )
                _ensure_cold_loaded_for_context(
                    context_id=str(state.get("context_id") or state.get("_context_id") or "default"),
                    agent_id=str(agent_id),
                    agent=agent,
                    redis_client=_resolve_cold_redis_client(state),
                )
            except Exception as exc:
                _record_cold_error(agent, "load_failed", exc)
        derived_parts, scan_metrics = _build_derived_context_parts(
            agent=agent,
            agents=agents,
            own_id=agent_id,
            locations=locations,
            traders=traders,
            world_turn=world_turn,
            target_id=target_id,
            visible_entities=visible_entities,
            deep_debug=deep_debug,
        )
        if not bypass_cache:
            _store_context_cache(
                agent,
                cache_key=cache_key,
                derived=derived_parts,
                world_turn=world_turn,
            )

    metrics["context_builder_memory_scan_records"] = int(
        metrics.get("context_builder_memory_scan_records", 0)
    ) + int(scan_metrics.get("memory_scan_records", 0))
    metrics["context_builder_knowledge_entries_scanned"] = int(
        metrics.get("context_builder_knowledge_entries_scanned", 0)
    ) + int(scan_metrics.get("knowledge_entries_scanned", 0))
    metrics["context_builder_memory_fallbacks"] = int(
        metrics.get("context_builder_memory_fallbacks", 0)
    ) + int(scan_metrics.get("memory_fallbacks", 0))
    metrics["context_builder_memory_fallback_records_scanned"] = int(
        metrics.get("context_builder_memory_fallback_records_scanned", 0)
    ) + int(scan_metrics.get("memory_fallback_records_scanned", 0))
    metrics["context_builder_knowledge_primary_hits"] = int(
        metrics.get("context_builder_knowledge_primary_hits", 0)
    ) + int(scan_metrics.get("knowledge_primary_hits", 0))

    # ── Known targets ─────────────────────────────────────────────────────────
    known_targets: list[dict[str, Any]] = _targets_from_agent(agent, agents)

    # ── Current commitment (active scheduled_action) ──────────────────────────
    current_commitment: dict[str, Any] | None = agent.get("scheduled_action")

    # ── Social context (lazy — from state["relations"] if present) ───────────
    social_context: dict[str, Any] | None = _social_context_for(agent_id, state)

    # ── Group context (lazy — from state["groups"] if present) ───────────────
    group_context: dict[str, Any] | None = _group_context_for(agent_id, state)

    context = AgentContext(
        agent_id=agent_id,
        self_state=agent,
        location_state=loc,
        world_context=world_context,
        visible_entities=visible_entities,
        known_entities=list(derived_parts.get("known_entities", [])),
        known_locations=list(derived_parts.get("known_locations", [])),
        known_hazards=list(derived_parts.get("known_hazards", [])),
        known_traders=list(derived_parts.get("known_traders", [])),
        known_targets=known_targets,
        current_commitment=current_commitment,
        combat_context=combat_context,
        social_context=social_context,
        group_context=group_context,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    metrics["context_builder_ms"] = float(metrics.get("context_builder_ms", 0.0)) + elapsed_ms
    calls = int(metrics.get("context_builder_calls", 0))
    hits = int(metrics.get("context_builder_cache_hits", 0))
    metrics["context_builder_cache_hit_rate"] = (hits / calls) if calls > 0 else 0.0
    return context


# ── Private helpers ────────────────────────────────────────────────────────────

_MEMORY_V3_DETAIL_ENTITY_KEYS: tuple[str, ...] = (
    "target_agent_id",
    "subject",
    "agent_id",
    "other_agent_id",
    "target_id",
    "dead_agent_id",
)
_MEMORY_V3_DETAIL_LOCATION_KEYS: tuple[str, ...] = (
    "location_id",
    "destination",
    "from_location",
    "to_location",
    "corpse_location_id",
    "reported_corpse_location_id",
)
_HAZARD_KINDS: frozenset[str] = frozenset({
    "emission_warning",
    "emission_started",
    "emission_ended",
    "anomaly_detected",
})


def _ensure_context_builder_metrics(agent: dict[str, Any]) -> dict[str, Any]:
    metrics = agent.setdefault("brain_context_metrics", {})
    if not isinstance(metrics, dict):
        metrics = {}
        agent["brain_context_metrics"] = metrics
    metrics.setdefault("context_builder_calls", 0)
    metrics.setdefault("context_builder_cache_hits", 0)
    metrics.setdefault("context_builder_cache_misses", 0)
    metrics.setdefault("context_builder_cache_hit_rate", 0.0)
    metrics.setdefault("context_builder_memory_scan_records", 0)
    metrics.setdefault("context_builder_knowledge_entries_scanned", 0)
    metrics.setdefault("context_builder_memory_fallbacks", 0)
    metrics.setdefault("context_builder_memory_fallback_records_scanned", 0)
    metrics.setdefault("context_builder_knowledge_primary_hits", 0)
    metrics.setdefault("context_builder_ms", 0.0)
    return metrics


def _resolve_objective_key(agent: dict[str, Any]) -> str:
    active_objective = agent.get("active_objective")
    if isinstance(active_objective, dict):
        key = str(active_objective.get("key") or "")
        if key:
            return key
    brain_ctx = agent.get("brain_v3_context")
    if isinstance(brain_ctx, dict):
        key = str(brain_ctx.get("objective_key") or "")
        if key:
            return key
    return str(agent.get("current_goal") or "")


def _resolve_memory_revision(agent: dict[str, Any]) -> int:
    memory_v3 = agent.get("memory_v3")
    if not isinstance(memory_v3, dict):
        return 0
    stats = memory_v3.get("stats")
    if not isinstance(stats, dict):
        return 0
    return int(stats.get("memory_revision", 0) or 0)


def _resolve_knowledge_major_revision(agent: dict[str, Any]) -> int:
    knowledge = agent.get("knowledge_v1")
    if not isinstance(knowledge, dict):
        return 0
    return int(knowledge.get("major_revision", knowledge.get("revision", 0)) or 0)


def _resolve_emission_phase(state: dict[str, Any]) -> str:
    if state.get("emission_active"):
        return "active"
    if state.get("emission_scheduled_turn") is not None:
        return "scheduled"
    return "none"


def _build_context_cache_key(
    *,
    agent: dict[str, Any],
    state: dict[str, Any],
    world_turn: int,
    location_id: str,
    objective_key: str,
    target_id: str | None,
) -> dict[str, Any]:
    # memory_revision is always included for correctness (MVP).
    # When knowledge_v1 fully covers derived context, memory_revision can be
    # excluded to improve hit-rate.  Left as a future optimization once
    # knowledge-first coverage is confirmed complete.
    return {
        "knowledge_major_revision": _resolve_knowledge_major_revision(agent),
        "memory_revision": _resolve_memory_revision(agent),
        "world_turn_bucket": world_turn // CONTEXT_CACHE_TURN_BUCKET_SIZE,
        "location_id": location_id,
        "objective_key": objective_key,
        "target_id": target_id,
        "global_goal": str(agent.get("global_goal") or ""),
        "emission_phase": _resolve_emission_phase(state),
    }


def _should_scan_memory_for_context(
    agent: dict[str, Any],
    *,
    target_id: str | None,
    deep_debug: bool = False,
) -> bool:
    if deep_debug:
        return True
    knowledge = agent.get("knowledge_v1")
    if not isinstance(knowledge, dict):
        return True

    known_npcs = knowledge.get("known_npcs")
    known_locations = knowledge.get("known_locations")
    known_traders = knowledge.get("known_traders")
    known_hazards = knowledge.get("known_hazards")
    known_corpses = knowledge.get("known_corpses")
    hunt_evidence = knowledge.get("hunt_evidence")

    has_routine_knowledge = all(
        isinstance(table, dict) and bool(table)
        for table in (known_npcs, known_locations, known_traders, known_hazards)
    )
    has_corpse_knowledge = isinstance(known_corpses, dict) and bool(known_corpses)
    if not has_routine_knowledge and not has_corpse_knowledge:
        return True

    if target_id:
        has_known_target = isinstance(known_npcs, dict) and isinstance(known_npcs.get(target_id), dict)
        has_hunt_target = isinstance(hunt_evidence, dict) and isinstance(hunt_evidence.get(target_id), dict)
        has_target_corpse = (
            isinstance(known_corpses, dict)
            and any(
                isinstance(entry, dict)
                and str(entry.get("dead_agent_id") or "") == target_id
                and not bool(entry.get("is_stale"))
                for entry in known_corpses.values()
            )
        )
        if not (has_known_target or has_hunt_target or has_target_corpse):
            return True

    return False


def _is_critical_survival_state(agent: dict[str, Any]) -> bool:
    hp = int(agent.get("hp", 100) or 100)
    hunger = int(agent.get("hunger", 0) or 0)
    thirst = int(agent.get("thirst", 0) or 0)
    sleepiness = int(agent.get("sleepiness", 0) or 0)
    return (
        hp <= CRITICAL_HP_THRESHOLD
        or hunger >= CRITICAL_HUNGER_THRESHOLD
        or thirst >= CRITICAL_THIRST_THRESHOLD
        or sleepiness >= CRITICAL_REST_THRESHOLD
    )


def _should_bypass_context_cache(
    *,
    agent: dict[str, Any],
    state: dict[str, Any],
    combat_context: dict[str, Any] | None,
    force_refresh: bool,
    deep_debug: bool = False,
) -> bool:
    if force_refresh:
        return True
    if deep_debug:
        return True
    if combat_context is not None:
        return True
    if _is_critical_survival_state(agent):
        return True
    if not agent.get("is_alive", True):
        return True
    if agent.get("has_left_zone"):
        return True
    debug = state.get("debug")
    if isinstance(debug, dict) and debug.get("deep_context_builder", False):
        return True
    return False


def _cache_key_matches(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return left == right


def _get_context_cache(
    agent: dict[str, Any],
    cache_key: dict[str, Any],
    *,
    world_turn: int,
) -> dict[str, list[dict[str, Any]]] | None:
    cache = agent.get("brain_context_cache")
    if not isinstance(cache, dict):
        return None
    cached_key = cache.get("cache_key")
    if not isinstance(cached_key, dict) or not _cache_key_matches(cached_key, cache_key):
        return None
    derived = cache.get("derived")
    if not isinstance(derived, dict):
        return None
    cache["hits"] = int(cache.get("hits", 0)) + 1
    cache["last_used_turn"] = world_turn
    return {
        "known_entities": copy.deepcopy(derived.get("known_entities", [])),
        "known_locations": copy.deepcopy(derived.get("known_locations", [])),
        "known_traders": copy.deepcopy(derived.get("known_traders", [])),
        "known_hazards": copy.deepcopy(derived.get("known_hazards", [])),
        "target_leads": copy.deepcopy(derived.get("target_leads", [])),
        "corpse_leads": copy.deepcopy(derived.get("corpse_leads", [])),
    }


def _store_context_cache(
    agent: dict[str, Any],
    *,
    cache_key: dict[str, Any],
    derived: dict[str, list[dict[str, Any]]],
    world_turn: int,
) -> None:
    agent["brain_context_cache"] = {
        "cache_key": dict(cache_key),
        "derived": {
            "known_entities": copy.deepcopy(derived.get("known_entities", [])),
            "known_locations": copy.deepcopy(derived.get("known_locations", [])),
            "known_traders": copy.deepcopy(derived.get("known_traders", [])),
            "known_hazards": copy.deepcopy(derived.get("known_hazards", [])),
            "target_leads": copy.deepcopy(derived.get("target_leads", [])),
            "corpse_leads": copy.deepcopy(derived.get("corpse_leads", [])),
        },
        "created_turn": world_turn,
        "last_used_turn": world_turn,
        "hits": 0,
    }


def _build_derived_context_parts(
    *,
    agent: dict[str, Any],
    agents: dict[str, Any],
    own_id: str,
    locations: dict[str, Any],
    traders: dict[str, Any],
    world_turn: int,
    target_id: str | None,
    visible_entities: list[dict[str, Any]],
    deep_debug: bool = False,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    from app.games.zone_stalkers.knowledge.knowledge_builder import (  # noqa: PLC0415
        build_known_entities_from_knowledge,
        build_known_hazards_from_knowledge,
        build_known_locations_from_knowledge,
        build_known_traders_from_knowledge,
    )

    knowledge = agent.get("knowledge_v1")
    knowledge_entries_scanned = 0
    if isinstance(knowledge, dict):
        knowledge_entries_scanned += len(knowledge.get("known_npcs", {}) or {})
        knowledge_entries_scanned += len(knowledge.get("known_locations", {}) or {})
        knowledge_entries_scanned += len(knowledge.get("known_traders", {}) or {})
        knowledge_entries_scanned += len(knowledge.get("known_hazards", {}) or {})

    should_scan_memory = _should_scan_memory_for_context(
        agent,
        target_id=target_id,
        deep_debug=deep_debug,
    )
    memory_records = _memory_v3_records(agent) if should_scan_memory else []
    memory_scan_records = len(memory_records)

    knowledge_entities = build_known_entities_from_knowledge(
        agent, world_turn, agents=agents, own_id=own_id, target_id=target_id
    )
    memory_entities = _entities_from_memory(
        agent, agents, own_id, memory_records=memory_records
    )
    known_entities = _merge_entities_by_id(knowledge_entities, memory_entities)

    knowledge_locations = build_known_locations_from_knowledge(
        agent, world_turn, locations=locations, traders=traders
    )
    memory_locations = _locations_from_memory(
        agent, locations, traders, memory_records=memory_records
    )
    known_locations = _merge_locations_by_id(knowledge_locations, memory_locations)

    knowledge_hazards = build_known_hazards_from_knowledge(agent, world_turn)
    memory_hazards = _hazards_from_memory(agent, memory_records=memory_records)
    known_hazards = _merge_hazards(knowledge_hazards, memory_hazards)

    visible_traders: list[dict[str, Any]] = []
    for entity in visible_entities:
        if entity.get("is_trader"):
            aid = entity["agent_id"]
            visible_traders.append({
                "agent_id": aid,
                "name": entity.get("name", aid),
                "source": "visible",
            })
    knowledge_traders = build_known_traders_from_knowledge(agent, world_turn, traders_dict=traders)
    memory_traders = _traders_from_memory(agent, traders, memory_records=memory_records)
    known_traders = _merge_traders_by_id(visible_traders, knowledge_traders, memory_traders)

    target_leads = _build_target_leads(agent, target_id, memory_records=memory_records)
    corpse_leads = _build_corpse_leads(agent, target_id, memory_records=memory_records)

    return (
        {
            "known_entities": known_entities,
            "known_locations": known_locations,
            "known_hazards": known_hazards,
            "known_traders": known_traders,
            "target_leads": target_leads,
            "corpse_leads": corpse_leads,
        },
        {
            "memory_scan_records": memory_scan_records,
            "knowledge_entries_scanned": knowledge_entries_scanned,
            "memory_fallbacks": 1 if should_scan_memory else 0,
            "memory_fallback_records_scanned": memory_scan_records,
            "knowledge_primary_hits": 0 if should_scan_memory else 1,
        },
    )


def _memory_v3_records(agent: dict[str, Any]) -> list[dict[str, Any]]:
    memory_v3 = agent.get("memory_v3")
    if not isinstance(memory_v3, dict):
        return []
    records = memory_v3.get("records")
    if not isinstance(records, dict) or not records:
        return []
    return sorted(
        (record for record in records.values() if isinstance(record, dict)),
        key=lambda record: (
            int(record.get("created_turn", 0) or 0),
            str(record.get("id", "")),
        ),
    )


def _record_memory_turn(record: dict[str, Any]) -> int:
    return int(record.get("created_turn", 0) or 0)


def _record_details(record: dict[str, Any]) -> dict[str, Any]:
    details = record.get("details")
    return details if isinstance(details, dict) else {}


def _record_is_active(record: dict[str, Any]) -> bool:
    return str(record.get("status", "active")) not in {"stale", "archived", "contradicted"}


def _record_entity_ids(record: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for entity_id in record.get("entity_ids", []) or []:
        if entity_id:
            ids.append(str(entity_id))
    details = _record_details(record)
    for key in _MEMORY_V3_DETAIL_ENTITY_KEYS:
        value = details.get(key)
        if value:
            ids.append(str(value))
    return list(dict.fromkeys(ids))


def _record_location_ids(record: dict[str, Any]) -> list[str]:
    locations: list[str] = []
    location_id = record.get("location_id")
    if location_id:
        locations.append(str(location_id))
    details = _record_details(record)
    for key in _MEMORY_V3_DETAIL_LOCATION_KEYS:
        value = details.get(key)
        if value:
            locations.append(str(value))
    return list(dict.fromkeys(locations))


def _record_trader_id(record: dict[str, Any], traders: dict[str, Any]) -> str | None:
    details = _record_details(record)
    trader_id = details.get("trader_id")
    if trader_id:
        return str(trader_id)
    tags = {str(tag) for tag in record.get("tags", []) or []}
    if "trader" not in tags and record.get("kind") != "trader_visited":
        return None
    for entity_id in _record_entity_ids(record):
        if entity_id in traders:
            return entity_id
        if entity_id.startswith("trader_"):
            return entity_id
    return None

# ── Knowledge + memory merge wrappers (PR3) ───────────────────────────────────
# These merge knowledge_v1 tables with memory_v3 fallback scan results.
# On duplicate ids/keys, knowledge_v1 entries win.
# This allows gradual migration without breaking existing gameplay logic.


def _entities_from_knowledge_or_memory(
    agent: dict[str, Any],
    agents: dict[str, Any],
    own_id: str,
    world_turn: int,
    target_id: str | None,
) -> list[dict[str, Any]]:
    """Merge knowledge_v1 with memory_v3 fallback; knowledge has priority."""
    from app.games.zone_stalkers.knowledge.knowledge_builder import (  # noqa: PLC0415
        build_known_entities_from_knowledge,
    )
    knowledge_entities = build_known_entities_from_knowledge(
        agent, world_turn, agents=agents, own_id=own_id, target_id=target_id
    )
    memory_entities = _entities_from_memory(agent, agents, own_id)
    return _merge_entities_by_id(knowledge_entities, memory_entities)


def _locations_from_knowledge_or_memory(
    agent: dict[str, Any],
    locations: dict[str, Any],
    traders: dict[str, Any] | None,
    world_turn: int,
) -> list[dict[str, Any]]:
    """Merge knowledge_v1 with memory_v3 fallback; knowledge has priority."""
    from app.games.zone_stalkers.knowledge.knowledge_builder import (  # noqa: PLC0415
        build_known_locations_from_knowledge,
    )
    knowledge_locs = build_known_locations_from_knowledge(
        agent, world_turn, locations=locations, traders=traders
    )
    memory_locs = _locations_from_memory(agent, locations, traders)
    return _merge_locations_by_id(knowledge_locs, memory_locs)


def _hazards_from_knowledge_or_memory(
    agent: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Merge knowledge_v1 with memory_v3 fallback; knowledge has priority."""
    from app.games.zone_stalkers.knowledge.knowledge_builder import (  # noqa: PLC0415
        build_known_hazards_from_knowledge,
    )
    knowledge_hazards = build_known_hazards_from_knowledge(agent, world_turn)
    memory_hazards = _hazards_from_memory(agent)
    return _merge_hazards(knowledge_hazards, memory_hazards)


def _traders_from_knowledge_or_memory(
    visible: list[dict[str, Any]],
    agent: dict[str, Any],
    locations: dict[str, Any],
    traders_dict: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Merge visible + knowledge_v1 + memory_v3 traders with dedupe by agent_id."""
    from app.games.zone_stalkers.knowledge.knowledge_builder import (  # noqa: PLC0415
        build_known_traders_from_knowledge,
    )
    visible_traders: list[dict[str, Any]] = []
    for entity in visible:
        if entity.get("is_trader"):
            aid = entity["agent_id"]
            visible_traders.append({
                "agent_id": aid,
                "name": entity.get("name", aid),
                "source": "visible",
            })

    # From knowledge_v1 tables.
    knowledge_traders = build_known_traders_from_knowledge(agent, world_turn, traders_dict=traders_dict)
    memory_traders = _traders_from_memory(agent, traders_dict)
    return _merge_traders_by_id(visible_traders, knowledge_traders, memory_traders)


def _merge_entities_by_id(
    knowledge_entities: list[dict[str, Any]],
    memory_entities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entity in knowledge_entities:
        aid = str(entity.get("agent_id") or "")
        if not aid or aid in seen:
            continue
        merged.append(entity)
        seen.add(aid)
    for entity in memory_entities:
        aid = str(entity.get("agent_id") or "")
        if not aid or aid in seen:
            continue
        merged.append(entity)
        seen.add(aid)
    return merged


def _merge_locations_by_id(
    knowledge_locations: list[dict[str, Any]],
    memory_locations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for loc in knowledge_locations:
        lid = str(loc.get("location_id") or "")
        if not lid or lid in seen:
            continue
        merged.append(loc)
        seen.add(lid)
    for loc in memory_locations:
        lid = str(loc.get("location_id") or "")
        if not lid or lid in seen:
            continue
        merged.append(loc)
        seen.add(lid)
    return merged


def _merge_traders_by_id(
    visible_traders: list[dict[str, Any]],
    knowledge_traders: list[dict[str, Any]],
    memory_traders: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for trader in visible_traders:
        tid = str(trader.get("agent_id") or "")
        if not tid or tid in seen:
            continue
        merged.append(trader)
        seen.add(tid)
    for trader in knowledge_traders:
        tid = str(trader.get("agent_id") or "")
        if not tid or tid in seen:
            continue
        merged.append(trader)
        seen.add(tid)
    for trader in memory_traders:
        tid = str(trader.get("agent_id") or "")
        if not tid or tid in seen:
            continue
        merged.append(trader)
        seen.add(tid)
    return merged


def _hazard_key(hazard: dict[str, Any]) -> str:
    kind = str(hazard.get("kind") or "")
    location_id = str(
        hazard.get("location_id")
        or hazard.get("effects", {}).get("location_id")
        or ""
    )
    return f"{location_id}:{kind}"


def _merge_hazards(
    knowledge_hazards: list[dict[str, Any]],
    memory_hazards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for hazard in knowledge_hazards:
        key = _hazard_key(hazard)
        if not key or key in seen:
            continue
        merged.append(hazard)
        seen.add(key)
    for hazard in memory_hazards:
        key = _hazard_key(hazard)
        if not key or key in seen:
            continue
        merged.append(hazard)
        seen.add(key)
    return merged



def _entities_from_memory(
    agent: dict[str, Any],
    agents: dict[str, Any],
    own_id: str,
    *,
    memory_records: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return a deduplicated list of agents mentioned in the NPC's memory.

    Performance note: iterates memory once (O(M)).
    This is acceptable at Phase 1 but could be optimised in Phase 5+ with a
    memory index keyed by observed agent_id.
    """
    seen_ids: set[str] = set()
    result: list[dict[str, Any]] = []
    records = memory_records if memory_records is not None else _memory_v3_records(agent)
    for record in records:
        if not _record_is_active(record):
            continue
        last_known_location = None
        for location_id in _record_location_ids(record):
            if location_id:
                last_known_location = location_id
                break
        for other_id in _record_entity_ids(record):
            if (
                other_id
                and other_id != own_id
                and other_id not in seen_ids
                and other_id in agents
            ):
                seen_ids.add(other_id)
                other = agents[other_id]
                result.append({
                    "agent_id": other_id,
                    "name": other.get("name", other_id),
                    "archetype": other.get("archetype"),
                    "is_alive": other.get("is_alive", True),
                    "last_known_location": last_known_location,
                    "memory_turn": _record_memory_turn(record),
                })
    return result


def _locations_from_memory(
    agent: dict[str, Any],
    locations: dict[str, Any],
    traders: dict[str, Any] | None = None,
    *,
    memory_records: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return a deduplicated list of locations the NPC has in memory.

    Parameters
    ----------
    traders
        The ``state["traders"]`` dict (optional; used for ``has_trader`` check).
        When provided, ``has_trader`` is ``True`` if any trader in this dict
        has ``location_id == loc_id``.
    """
    if traders is None:
        traders = {}
    seen_ids: set[str] = set()
    result: list[dict[str, Any]] = []
    records = memory_records if memory_records is not None else _memory_v3_records(agent)
    for record in records:
        if not _record_is_active(record):
            continue
        for loc_id in _record_location_ids(record):
            if loc_id and loc_id not in seen_ids and loc_id in locations:
                seen_ids.add(loc_id)
                loc = locations[loc_id]
                has_trader = any(
                    trader.get("location_id") == loc_id
                    for trader in traders.values()
                    if isinstance(trader, dict)
                )
                result.append({
                    "location_id": loc_id,
                    "name": loc.get("name", loc_id),
                    "terrain_type": loc.get("terrain_type"),
                    "anomaly_activity": loc.get("anomaly_activity", 0),
                    "has_trader": has_trader,
                    "memory_turn": _record_memory_turn(record),
                })
    return result


def _hazards_from_memory(
    agent: dict[str, Any],
    *,
    memory_records: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return hazard observations recorded in the NPC's memory."""
    hazards: list[dict[str, Any]] = []
    records = memory_records if memory_records is not None else _memory_v3_records(agent)
    for record in records:
        if not _record_is_active(record):
            continue
        if str(record.get("kind", "")) not in _HAZARD_KINDS:
            continue
        hazards.append({
            "kind": record.get("kind"),
            "world_turn": _record_memory_turn(record),
            "effects": _record_details(record),
        })
    return hazards


_TARGET_LEAD_KINDS: frozenset[str] = frozenset({
    "target_seen",
    "target_intel",
    "target_last_known_location",
})
_CORPSE_LEAD_KINDS: frozenset[str] = frozenset({
    "corpse_seen",
    "target_corpse_reported",
    "target_corpse_seen",
})


def _build_target_leads(
    agent: dict[str, Any],
    target_id: str | None,
    *,
    memory_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return location leads for a hunt target from knowledge_v1 and memory_v3."""
    leads: list[dict[str, Any]] = []
    if not target_id:
        return leads

    knowledge = agent.get("knowledge_v1")
    if isinstance(knowledge, dict):
        npc_entry = knowledge.get("known_npcs", {}).get(target_id)
        if isinstance(npc_entry, dict):
            leads.append({
                "source": "knowledge_v1",
                "agent_id": target_id,
                "last_seen_location_id": npc_entry.get("last_seen_location_id"),
                "last_seen_turn": npc_entry.get("last_seen_turn"),
                "confidence": npc_entry.get("confidence"),
                "is_alive": npc_entry.get("is_alive", True),
            })
        hunt_entry = knowledge.get("hunt_evidence", {}).get(target_id)
        if isinstance(hunt_entry, dict):
            last_seen = hunt_entry.get("last_seen")
            if isinstance(last_seen, dict):
                leads.append({
                    "source": "knowledge_v1",
                    "agent_id": target_id,
                    "last_seen_location_id": last_seen.get("location_id"),
                    "last_seen_turn": last_seen.get("turn"),
                    "confidence": last_seen.get("confidence"),
                    "is_alive": True,
                })
            death = hunt_entry.get("death")
            if isinstance(death, dict):
                leads.append({
                    "source": "knowledge_v1",
                    "kind": death.get("status"),
                    "location_id": death.get("location_id"),
                    "world_turn": death.get("turn"),
                })
        for corpse in knowledge.get("known_corpses", {}).values():
            if not isinstance(corpse, dict):
                continue
            if str(corpse.get("dead_agent_id") or "") != target_id or bool(corpse.get("is_stale")):
                continue
            leads.append({
                "source": "knowledge_v1",
                "kind": "target_corpse_seen",
                "location_id": corpse.get("location_id"),
                "world_turn": corpse.get("last_seen_turn") or corpse.get("first_seen_turn"),
            })

    for record in memory_records:
        if not _record_is_active(record):
            continue
        if str(record.get("kind", "")) not in _TARGET_LEAD_KINDS:
            continue
        details = _record_details(record)
        # Only include if it matches our target (or has no specific target)
        rec_target = str(
            details.get("target_agent_id") or details.get("target_id") or ""
        )
        if rec_target and rec_target != target_id:
            continue
        leads.append({
            "source": "memory_v3",
            "kind": record.get("kind"),
            "location_id": record.get("location_id") or details.get("location_id"),
            "world_turn": _record_memory_turn(record),
        })
    return leads


def _build_corpse_leads(
    agent: dict[str, Any],
    target_id: str | None,
    *,
    memory_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return corpse-location leads for a target from knowledge_v1 and memory_v3."""
    leads: list[dict[str, Any]] = []
    knowledge = agent.get("knowledge_v1")
    if isinstance(knowledge, dict):
        for corpse in knowledge.get("known_corpses", {}).values():
            if not isinstance(corpse, dict):
                continue
            if bool(corpse.get("is_stale")):
                continue
            if target_id and str(corpse.get("dead_agent_id") or "") != target_id:
                continue
            leads.append({
                "source": "knowledge_v1",
                "kind": "target_corpse_seen",
                "location_id": corpse.get("location_id"),
                "world_turn": corpse.get("last_seen_turn") or corpse.get("first_seen_turn"),
            })
    for record in memory_records:
        if not _record_is_active(record):
            continue
        if str(record.get("kind", "")) not in _CORPSE_LEAD_KINDS:
            continue
        details = _record_details(record)
        if target_id:
            rec_target = str(
                details.get("target_agent_id")
                or details.get("target_id")
                or details.get("dead_agent_id")
                or ""
            )
            if rec_target and rec_target != target_id:
                continue
        leads.append({
            "source": "memory_v3",
            "kind": record.get("kind"),
            "location_id": (
                record.get("location_id")
                or details.get("corpse_location_id")
                or details.get("reported_corpse_location_id")
                or details.get("location_id")
            ),
            "world_turn": _record_memory_turn(record),
        })
    return leads


def _traders_from_visible_and_memory(
    visible: list[dict[str, Any]],
    agent: dict[str, Any],
    locations: dict[str, Any],
    traders_dict: dict[str, Any],
    *,
    memory_records: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Collect known trader info from co-located agents and memory."""
    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Co-located traders
    for entity in visible:
        if entity.get("is_trader"):
            aid = entity["agent_id"]
            seen.add(aid)
            result.append({"agent_id": aid, "name": entity.get("name", aid), "source": "visible"})

    records = memory_records if memory_records is not None else _memory_v3_records(agent)
    for record in records:
        if not _record_is_active(record):
            continue
        trader_id = _record_trader_id(record, traders_dict)
        if trader_id and trader_id not in seen:
            seen.add(trader_id)
            details = _record_details(record)
            result.append({
                "agent_id": trader_id,
                "name": details.get("trader_name")
                or traders_dict.get(trader_id, {}).get("name")
                or trader_id,
                "location_id": record.get("location_id")
                or details.get("location_id")
                or traders_dict.get(trader_id, {}).get("location_id"),
                "source": "memory_v3",
                "memory_turn": _record_memory_turn(record),
            })
    return result


def _traders_from_memory(
    agent: dict[str, Any],
    traders_dict: dict[str, Any],
    *,
    memory_records: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    records = memory_records if memory_records is not None else _memory_v3_records(agent)
    for record in records:
        if not _record_is_active(record):
            continue
        trader_id = _record_trader_id(record, traders_dict)
        if not trader_id or trader_id in seen:
            continue
        seen.add(trader_id)
        details = _record_details(record)
        result.append({
            "agent_id": trader_id,
            "name": details.get("trader_name")
            or traders_dict.get(trader_id, {}).get("name")
            or trader_id,
            "location_id": record.get("location_id")
            or details.get("location_id")
            or traders_dict.get(trader_id, {}).get("location_id"),
            "source": "memory_v3",
            "memory_turn": _record_memory_turn(record),
        })
    return result


def _targets_from_agent(
    agent: dict[str, Any],
    agents: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return hunt/kill targets for the agent."""
    targets: list[dict[str, Any]] = []
    kill_target_id = agent.get("kill_target_id")
    if kill_target_id and kill_target_id in agents:
        target = agents[kill_target_id]
        targets.append({
            "agent_id": kill_target_id,
            "name": target.get("name", kill_target_id),
            "is_alive": target.get("is_alive", True),
            "location_id": target.get("location_id"),
        })
    return targets


def _combat_context_for(
    agent_id: str,
    state: dict[str, Any],
) -> dict[str, Any] | None:
    """Return the combat interaction for this agent, if active."""
    combat_interactions = state.get("combat_interactions", {})
    return combat_interactions.get(agent_id)


def _social_context_for(
    agent_id: str,
    state: dict[str, Any],
) -> dict[str, Any] | None:
    """Return relevant relation data for this agent (lazy, Phase 6+)."""
    relations = state.get("relations", {})
    agent_relations = relations.get(agent_id)
    if not agent_relations:
        return None
    return {"relations": agent_relations}


def _group_context_for(
    agent_id: str,
    state: dict[str, Any],
) -> dict[str, Any] | None:
    """Return the group this agent belongs to, if any (Phase 7+)."""
    groups = state.get("groups", {})
    for group_id, group in groups.items():
        if agent_id in group.get("members", []):
            return {"group_id": group_id, "group": group}
    return None
