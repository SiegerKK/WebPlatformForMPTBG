"""RelationState — social relationship between two agents.

Social invariant (11.3 from spec):
    Relationships may only change through:
    - an in-game event (combat, observation, trade),
    - a dialogue session,
    - group membership / faction affinity.

``state["relations"][agent_id][other_id]`` stores these lazily.
Missing pairs default to neutral (see ``social/relations.py::get_relation``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# ── Valid attitude values ─────────────────────────────────────────────────────
ATTITUDE_ALLY = "ally"
ATTITUDE_FRIENDLY = "friendly"
ATTITUDE_NEUTRAL = "neutral"
ATTITUDE_SUSPICIOUS = "suspicious"
ATTITUDE_HOSTILE = "hostile"
ATTITUDE_TARGET = "target"

ALL_ATTITUDES: tuple[str, ...] = (
    ATTITUDE_ALLY,
    ATTITUDE_FRIENDLY,
    ATTITUDE_NEUTRAL,
    ATTITUDE_SUSPICIOUS,
    ATTITUDE_HOSTILE,
    ATTITUDE_TARGET,
)


@dataclass
class RelationState:
    """Directed social relationship: how ``owner_id`` perceives ``other_id``.

    All float fields are in the range [−1.0, 1.0] unless noted otherwise.

    Parameters
    ----------
    attitude
        Categorical summary: one of ``ATTITUDE_*`` constants.
    trust
        How much owner trusts other (−1 = betrayer, +1 = fully trusted).
    fear
        How much owner fears other (0 = fearless, +1 = terrified).
    respect
        Perceived competence / reliability of the other agent.
    hostility
        Active aggression toward the other (0 = none, +1 = maximum).
    debt
        Positive = owner owes other; negative = other owes owner.
    faction_bias
        Inherited bias from faction alignment (−1 to +1).
    shared_history_score
        Cumulative score of past interactions (positive = cooperative history).
    known_reliability
        How often other has kept promises / been predictable (0–1).
    last_interaction_type
        String label of the most recent interaction (``"combat"``, ``"trade"``,
        ``"dialogue"``, ``"observation"``, etc.).
    last_interaction_turn
        World turn of the most recent interaction.
    """

    attitude: str = ATTITUDE_NEUTRAL
    trust: float = 0.0
    fear: float = 0.0
    respect: float = 0.0
    hostility: float = 0.0
    debt: float = 0.0
    faction_bias: float = 0.0
    shared_history_score: float = 0.0
    known_reliability: float = 0.0
    last_interaction_type: Optional[str] = None
    last_interaction_turn: Optional[int] = None
