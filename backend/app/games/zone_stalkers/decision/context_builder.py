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

    # ── Known entities (from memory) ─────────────────────────────────────────
    known_entities: list[dict[str, Any]] = _entities_from_memory(agent, agents, agent_id)

    # ── Known locations (visited or mentioned in memory) ──────────────────────
    known_locations: list[dict[str, Any]] = _locations_from_memory(agent, locations, traders)

    # ── Known hazards (from memory) ───────────────────────────────────────────
    known_hazards: list[dict[str, Any]] = _hazards_from_memory(agent)

    # ── Known traders (co-located or from memory) ─────────────────────────────
    known_traders: list[dict[str, Any]] = _traders_from_visible_and_memory(
        visible_entities, agent, locations, traders
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
)
_MEMORY_V3_DETAIL_LOCATION_KEYS: tuple[str, ...] = (
    "location_id",
    "destination",
    "from_location",
    "to_location",
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


def _memory_v3_is_usable(agent: dict[str, Any]) -> bool:
    return bool(_memory_v3_records(agent))


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

def _entities_from_memory(
    agent: dict[str, Any],
    agents: dict[str, Any],
    own_id: str,
) -> list[dict[str, Any]]:
    """Return a deduplicated list of agents mentioned in the NPC's memory.

    Performance note: iterates memory once (O(M) where M ≤ MAX_AGENT_MEMORY=100 when enabled).
    This is acceptable at Phase 1 but could be optimised in Phase 5+ with a
    memory index keyed by observed agent_id.
    """
    if _memory_v3_is_usable(agent):
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

    seen_ids: set[str] = set()
    result: list[dict[str, Any]] = []
    for mem in agent.get("memory", []):
        effects = mem.get("effects", {})
        for key in ("target_agent_id", "subject", "agent_id"):
            other_id = effects.get(key)
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
                    "last_known_location": effects.get("location_id") or effects.get("to_location"),
                    "memory_turn": mem.get("world_turn", 0),
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
    if _memory_v3_is_usable(agent):
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

    seen_ids: set[str] = set()
    result: list[dict[str, Any]] = []
    for mem in agent.get("memory", []):
        effects = mem.get("effects", {})
        for key in ("location_id", "destination", "from_location", "to_location"):
            loc_id = effects.get(key)
            if loc_id and loc_id not in seen_ids and loc_id in locations:
                seen_ids.add(loc_id)
                loc = locations[loc_id]
                # P4 fix: check traders dict — location["agents"] is a list of IDs (strings),
                # so we cannot call .get() on those strings. Instead check state["traders"].
                loc_agent_ids: list[str] = loc.get("agents", [])
                has_trader = any(
                    tid in traders
                    for tid in loc_agent_ids
                )
                result.append({
                    "location_id": loc_id,
                    "name": loc.get("name", loc_id),
                    "terrain_type": loc.get("terrain_type"),
                    "anomaly_activity": loc.get("anomaly_activity", 0),
                    "has_trader": has_trader,
                    "memory_turn": mem.get("world_turn", 0),
                })
    return result


def _hazards_from_memory(agent: dict[str, Any]) -> list[dict[str, Any]]:
    """Return hazard observations recorded in the NPC's memory."""
    if _memory_v3_is_usable(agent):
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

    hazards: list[dict[str, Any]] = []
    for mem in agent.get("memory", []):
        if mem.get("type") != "observation":
            continue
        kind = mem.get("effects", {}).get("action_kind", "")
        if kind in (
            "emission_imminent",
            "emission_started",
            "emission_ended",
            "anomaly_detected",
        ):
            hazards.append({
                "kind": kind,
                "world_turn": mem.get("world_turn", 0),
                "effects": mem.get("effects", {}),
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

    if _memory_v3_is_usable(agent):
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

    # Traders remembered from memory
    for mem in agent.get("memory", []):
        if mem.get("type") != "observation":
            continue
        effects = mem.get("effects", {})
        if effects.get("action_kind") == "trader_visit":
            trader_id = effects.get("trader_id")
            if trader_id and trader_id not in seen:
                seen.add(trader_id)
                result.append({
                    "agent_id": trader_id,
                    "name": effects.get("trader_name", trader_id),
                    "location_id": effects.get("location_id"),
                    "source": "memory",
                    "memory_turn": mem.get("world_turn", 0),
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
