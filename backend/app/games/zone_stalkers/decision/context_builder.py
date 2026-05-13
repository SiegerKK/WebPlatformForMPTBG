"""context_builder — build AgentContext from raw world state.

``build_agent_context`` is called **once per tick per agent** before any
decision logic runs.  It must not mutate the state and must not make any
gameplay decisions.

Visibility model (Phase 1 MVP):
    An agent "sees":
    1. All other agents co-located at the same location.
    2. All objects (items, traders) at the same location.
    3. Agents / locations referenced in the agent's recent memory.
    4. Intel transferred in dialogue (Phase 6+).
    5. Signals from the group (Phase 7+).
"""
from __future__ import annotations

from typing import Any

from .models.agent_context import AgentContext


def build_agent_context(
    agent_id: str,
    agent: dict[str, Any],
    state: dict[str, Any],
) -> AgentContext:
    """Return a normalised ``AgentContext`` for the given agent.

    Parameters
    ----------
    agent_id
        The stable key of the agent in ``state["agents"]``.
    agent
        The agent dict (``state["agents"][agent_id]``).
    state
        The full world state dict (read-only semantics).

    Returns
    -------
    AgentContext
        Populated snapshot; never ``None``.
    """
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

    # ── Known entities — prefer knowledge_v1 tables, fallback to memory_v3 ─────
    world_turn: int = world_context["world_turn"]
    target_id: str | None = agent.get("kill_target_id") or None
    known_entities: list[dict[str, Any]] = _entities_from_knowledge_or_memory(
        agent, agents, agent_id, world_turn, target_id
    )

    # ── Known locations — prefer knowledge_v1, fallback to memory_v3 ─────────
    known_locations: list[dict[str, Any]] = _locations_from_knowledge_or_memory(
        agent, locations, traders, world_turn
    )

    # ── Known hazards — prefer knowledge_v1, fallback to memory_v3 ──────────
    known_hazards: list[dict[str, Any]] = _hazards_from_knowledge_or_memory(
        agent, world_turn
    )

    # ── Known traders — co-located + knowledge_v1, fallback to memory_v3 ────
    known_traders: list[dict[str, Any]] = _traders_from_knowledge_or_memory(
        visible_entities, agent, locations, traders, world_turn
    )

    # ── Known targets ─────────────────────────────────────────────────────────
    known_targets: list[dict[str, Any]] = _targets_from_agent(agent, agents)

    # ── Current commitment (active scheduled_action) ──────────────────────────
    current_commitment: dict[str, Any] | None = agent.get("scheduled_action")

    # ── Combat context ────────────────────────────────────────────────────────
    combat_context: dict[str, Any] | None = _combat_context_for(agent_id, state)

    # ── Social context (lazy — from state["relations"] if present) ───────────
    social_context: dict[str, Any] | None = _social_context_for(agent_id, state)

    # ── Group context (lazy — from state["groups"] if present) ───────────────
    group_context: dict[str, Any] | None = _group_context_for(agent_id, state)

    return AgentContext(
        agent_id=agent_id,
        self_state=agent,
        location_state=loc,
        world_context=world_context,
        visible_entities=visible_entities,
        known_entities=known_entities,
        known_locations=known_locations,
        known_hazards=known_hazards,
        known_traders=known_traders,
        known_targets=known_targets,
        current_commitment=current_commitment,
        combat_context=combat_context,
        social_context=social_context,
        group_context=group_context,
    )


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

# ── Knowledge-first wrappers (PR3) ────────────────────────────────────────────
# These try knowledge_v1 tables first, fall back to memory_v3 scan when
# knowledge_v1 is absent or empty.  This allows gradual migration without
# breaking any existing gameplay logic.


def _entities_from_knowledge_or_memory(
    agent: dict[str, Any],
    agents: dict[str, Any],
    own_id: str,
    world_turn: int,
    target_id: str | None,
) -> list[dict[str, Any]]:
    """Prefer knowledge_v1; fallback to memory_v3 scan."""
    from app.games.zone_stalkers.knowledge.knowledge_builder import (  # noqa: PLC0415
        build_known_entities_from_knowledge,
    )
    knowledge_entities = build_known_entities_from_knowledge(
        agent, world_turn, agents=agents, own_id=own_id, target_id=target_id
    )
    if knowledge_entities:
        return knowledge_entities
    # Fallback: old memory_v3 scan.
    return _entities_from_memory(agent, agents, own_id)


def _locations_from_knowledge_or_memory(
    agent: dict[str, Any],
    locations: dict[str, Any],
    traders: dict[str, Any] | None,
    world_turn: int,
) -> list[dict[str, Any]]:
    """Prefer knowledge_v1; fallback to memory_v3 scan."""
    from app.games.zone_stalkers.knowledge.knowledge_builder import (  # noqa: PLC0415
        build_known_locations_from_knowledge,
    )
    knowledge_locs = build_known_locations_from_knowledge(
        agent, world_turn, locations=locations, traders=traders
    )
    if knowledge_locs:
        return knowledge_locs
    return _locations_from_memory(agent, locations, traders)


def _hazards_from_knowledge_or_memory(
    agent: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Prefer knowledge_v1; fallback to memory_v3 scan."""
    from app.games.zone_stalkers.knowledge.knowledge_builder import (  # noqa: PLC0415
        build_known_hazards_from_knowledge,
    )
    knowledge_hazards = build_known_hazards_from_knowledge(agent, world_turn)
    if knowledge_hazards:
        return knowledge_hazards
    return _hazards_from_memory(agent)


def _traders_from_knowledge_or_memory(
    visible: list[dict[str, Any]],
    agent: dict[str, Any],
    locations: dict[str, Any],
    traders_dict: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Co-located traders first, then knowledge_v1; fallback to memory_v3 scan."""
    from app.games.zone_stalkers.knowledge.knowledge_builder import (  # noqa: PLC0415
        build_known_traders_from_knowledge,
    )
    # Always include co-located traders (visible).
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entity in visible:
        if entity.get("is_trader"):
            aid = entity["agent_id"]
            seen.add(aid)
            result.append({"agent_id": aid, "name": entity.get("name", aid), "source": "visible"})

    # From knowledge_v1 tables.
    knowledge_traders = build_known_traders_from_knowledge(agent, world_turn, traders_dict=traders_dict)
    for t in knowledge_traders:
        tid = t.get("agent_id", "")
        if tid and tid not in seen:
            seen.add(tid)
            result.append(t)
    if result:
        return result
    # Fallback: memory_v3 scan (excludes co-located already returned above).
    return _traders_from_visible_and_memory(visible, agent, locations, traders_dict)



def _entities_from_memory(
    agent: dict[str, Any],
    agents: dict[str, Any],
    own_id: str,
) -> list[dict[str, Any]]:
    """Return a deduplicated list of agents mentioned in the NPC's memory.

    Performance note: iterates memory once (O(M)).
    This is acceptable at Phase 1 but could be optimised in Phase 5+ with a
    memory index keyed by observed agent_id.
    """
    seen_ids: set[str] = set()
    result: list[dict[str, Any]] = []
    for record in _memory_v3_records(agent):
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
    for record in _memory_v3_records(agent):
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


def _hazards_from_memory(agent: dict[str, Any]) -> list[dict[str, Any]]:
    """Return hazard observations recorded in the NPC's memory."""
    hazards: list[dict[str, Any]] = []
    for record in _memory_v3_records(agent):
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


def _traders_from_visible_and_memory(
    visible: list[dict[str, Any]],
    agent: dict[str, Any],
    locations: dict[str, Any],
    traders_dict: dict[str, Any],
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

    for record in _memory_v3_records(agent):
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
