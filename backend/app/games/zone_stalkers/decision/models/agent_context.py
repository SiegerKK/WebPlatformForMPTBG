"""AgentContext — normalised snapshot of the world as seen by one agent.

Built once per tick per agent by ``context_builder.build_agent_context``.
Consumers (needs evaluator, intent selector, planner) must NOT mutate it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AgentContext:
    """Normalised world snapshot from the perspective of a single agent.

    Fields
    ------
    agent_id
        Stable identifier of the agent.
    self_state
        The agent dict extracted from ``state["agents"][agent_id]``.
    location_state
        The location dict the agent currently occupies.
    world_context
        Scalar world values: ``world_turn``, ``world_day``, ``world_hour``,
        ``world_minute``, ``emission_active``, ``emission_scheduled_turn``.
    visible_entities
        All *other* agents that share the current location (co-located).
    known_entities
        Agents the NPC has encountered or remembers from memory.
    known_locations
        Locations the NPC has visited or heard about in memory.
    known_hazards
        Hazards known from memory (anomalies, emissions, mutant sightings).
    known_traders
        Trader agents the NPC knows about (from memory or co-location).
    known_targets
        Agents that are current hunt/kill targets for this NPC.
    current_commitment
        The active ``scheduled_action`` dict if one exists, else ``None``.
    combat_context
        The active ``combat_interactions`` entry for this agent, or ``None``.
    social_context
        Relation data relevant to this agent.  ``None`` until Phase 6.
    group_context
        Group membership data for this agent.  ``None`` until Phase 7.
    """

    agent_id: str
    self_state: dict[str, Any]
    location_state: dict[str, Any]
    world_context: dict[str, Any]

    visible_entities: list[dict[str, Any]] = field(default_factory=list)
    known_entities: list[dict[str, Any]] = field(default_factory=list)
    known_locations: list[dict[str, Any]] = field(default_factory=list)
    known_hazards: list[dict[str, Any]] = field(default_factory=list)
    known_traders: list[dict[str, Any]] = field(default_factory=list)
    known_targets: list[dict[str, Any]] = field(default_factory=list)

    current_commitment: Optional[dict[str, Any]] = None
    combat_context: Optional[dict[str, Any]] = None
    social_context: Optional[dict[str, Any]] = None
    group_context: Optional[dict[str, Any]] = None
