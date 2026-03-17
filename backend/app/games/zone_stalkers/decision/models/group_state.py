"""GroupState — state of an NPC group.

Group invariant (11.4 from spec):
    A group always has:
    - at least one member,
    - a leader (may be elected on leader death),
    - a shared_goal,
    - an up-to-date status.

Group dissolution conditions:
    - Only one member remains.
    - Members' global goals diverge irreconcilably.
    - Leader lost and no successor can be elected.
    - Physical cohesion drops below threshold (members scattered).
    - Mutual hostility between members rises above threshold.

Leader succession score (from addendum):
    leader_score = (
        respect       * 0.30
        + trust       * 0.15
        + competence  * 0.25
        + health_ratio * 0.10
        + commitment   * 0.20
    )
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# ── Valid group statuses ───────────────────────────────────────────────────────
GROUP_STATUS_ACTIVE = "active"
GROUP_STATUS_REASSESSING = "reassessing"
GROUP_STATUS_DISSOLVED = "dissolved"

# ── Valid member roles ─────────────────────────────────────────────────────────
ROLE_LEADER = "leader"
ROLE_MEMBER = "member"
# Future roles (Phase 7+):
ROLE_SCOUT = "scout"
ROLE_SUPPORT = "support"
ROLE_DEPENDENT = "dependent_member"


@dataclass
class GroupState:
    """State of an NPC group.

    Parameters
    ----------
    group_id
        Unique identifier (e.g. ``"group_1"``).
    leader_id
        Agent ID of the current leader.
    members
        List of all member agent IDs (including the leader).
    shared_goal
        The global goal driving the group (``"get_rich"``, etc.).
    shared_plan
        Serialised group-level Plan dict (``None`` until Phase 7).
    hierarchy
        Mapping ``agent_id → role`` for all members.
    status
        One of ``GROUP_STATUS_*`` constants.
    formation_turn
        World turn when this group was formed.
    """

    group_id: str
    leader_id: str
    members: list[str] = field(default_factory=list)
    shared_goal: Optional[str] = None
    shared_plan: Optional[dict[str, Any]] = None
    hierarchy: dict[str, str] = field(default_factory=dict)
    status: str = GROUP_STATUS_ACTIVE
    formation_turn: Optional[int] = None
