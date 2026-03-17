"""relations — storage and retrieval of RelationState between agents.

Social invariant (11.3 from spec):
    Relations change only through events, dialogue, or group/faction changes.

Storage: ``state["relations"][owner_id][other_id]`` — lazily initialised.
Missing pairs default to neutral (``RelationState()``).
"""
from __future__ import annotations

from typing import Any

from ..models.relation_state import RelationState, ATTITUDE_NEUTRAL


def get_relation(
    owner_id: str,
    other_id: str,
    state: dict[str, Any],
) -> RelationState:
    """Return the RelationState from owner to other.

    Returns a default neutral relation if no entry exists yet.

    Parameters
    ----------
    owner_id
        The agent whose perspective this relation represents.
    other_id
        The agent being assessed.
    state
        The full world state dict.

    Returns
    -------
    RelationState
        Existing or default-neutral relation.
    """
    relations = state.get("relations", {})
    owner_relations = relations.get(owner_id, {})
    data = owner_relations.get(other_id)
    if data is None:
        return RelationState()
    # Support both dict (serialised) and RelationState instances
    if isinstance(data, RelationState):
        return data
    return RelationState(**data)


def set_relation(
    owner_id: str,
    other_id: str,
    relation: RelationState,
    state: dict[str, Any],
) -> None:
    """Persist a RelationState in the world state.

    Parameters
    ----------
    owner_id
        The owning agent.
    other_id
        The target agent.
    relation
        The RelationState to store.
    state
        The mutable world state dict.
    """
    state.setdefault("relations", {}).setdefault(owner_id, {})[other_id] = {
        "attitude": relation.attitude,
        "trust": relation.trust,
        "fear": relation.fear,
        "respect": relation.respect,
        "hostility": relation.hostility,
        "debt": relation.debt,
        "faction_bias": relation.faction_bias,
        "shared_history_score": relation.shared_history_score,
        "known_reliability": relation.known_reliability,
        "last_interaction_type": relation.last_interaction_type,
        "last_interaction_turn": relation.last_interaction_turn,
    }


def update_relation_from_event(
    owner_id: str,
    other_id: str,
    event_type: str,
    world_turn: int,
    state: dict[str, Any],
) -> None:
    """Apply a standard event-based delta to an existing relation.

    Supported event_type values:
        "combat_attacked"    — other attacked owner  → hostility+, trust−
        "combat_helped"      — other helped owner    → trust+, respect+
        "trade_completed"    → trust+, known_reliability+
        "memory_shared"      → trust+
        "warning_given"      → trust+, respect+
        "betrayal"           → hostility++, trust−−
        "group_formed"       — joined same group     → trust+, shared_history+
        "group_left"         — left group abruptly   → trust−

    Parameters
    ----------
    owner_id, other_id
        Direction of the relation being updated.
    event_type
        One of the string labels above.
    world_turn
        Current world turn (stored as last_interaction_turn).
    state
        Mutable world state.
    """
    rel = get_relation(owner_id, other_id, state)

    deltas: dict[str, dict[str, float]] = {
        "combat_attacked":   {"hostility": 0.2, "trust": -0.15, "fear": 0.1},
        "combat_helped":     {"trust": 0.1, "respect": 0.1},
        "trade_completed":   {"trust": 0.05, "known_reliability": 0.05},
        "memory_shared":     {"trust": 0.05},
        "warning_given":     {"trust": 0.08, "respect": 0.05},
        "betrayal":          {"hostility": 0.35, "trust": -0.30, "respect": -0.10},
        "group_formed":      {"trust": 0.10, "shared_history_score": 0.10},
        "group_left":        {"trust": -0.05},
    }

    delta = deltas.get(event_type, {})
    for attr, change in delta.items():
        current = getattr(rel, attr, 0.0)
        setattr(rel, attr, _clamp(current + change))

    rel.last_interaction_type = event_type
    rel.last_interaction_turn = world_turn

    # Recompute attitude from composite scores
    rel.attitude = _compute_attitude(rel)

    set_relation(owner_id, other_id, rel, state)


# ── Private helpers ────────────────────────────────────────────────────────────

def _clamp(value: float) -> float:
    return max(-1.0, min(1.0, value))


def _compute_attitude(rel: RelationState) -> str:
    """Derive the categorical attitude from numeric scores."""
    from ..models.relation_state import (
        ATTITUDE_ALLY, ATTITUDE_FRIENDLY, ATTITUDE_NEUTRAL,
        ATTITUDE_SUSPICIOUS, ATTITUDE_HOSTILE, ATTITUDE_TARGET,
    )
    if rel.attitude == ATTITUDE_TARGET:
        # ATTITUDE_TARGET is sticky: once set (e.g. via kill_stalker global goal),
        # it is not overridden by the automated attitude recomputation.
        # To clear it explicitly, set rel.attitude = ATTITUDE_NEUTRAL (or another
        # value) before calling set_relation(), e.g.:
        #   rel = get_relation(owner, other, state)
        #   rel.attitude = ATTITUDE_NEUTRAL
        #   set_relation(owner, other, rel, state)
        return ATTITUDE_TARGET  # sticky — only cleared explicitly
    score = rel.trust * 0.4 + rel.respect * 0.2 - rel.hostility * 0.4 + rel.shared_history_score * 0.2
    if score >= 0.6:
        return ATTITUDE_ALLY
    if score >= 0.3:
        return ATTITUDE_FRIENDLY
    if score <= -0.5:
        return ATTITUDE_HOSTILE
    if score <= -0.2:
        return ATTITUDE_SUSPICIOUS
    return ATTITUDE_NEUTRAL
