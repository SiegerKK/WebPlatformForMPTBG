"""group_state — GroupState storage and lifecycle management (Phase 7).

Group invariants (spec §11.4):
    - A group always has at least one member.
    - A group always has a leader.
    - A group always has a shared_goal.
    - A group always has an up-to-date status.

Dissolution conditions (addendum §11.1):
    - Only one member remains.
    - Members' global goals diverge.
    - Leader lost and no successor can be elected.
    - Physical cohesion drops below threshold (members scattered).
    - Mutual hostility between members exceeds threshold.

Leader succession score (addendum §11.2):
    leader_score =
        respect       * 0.30
        + trust       * 0.15
        + competence  * 0.25
        + health_ratio * 0.10
        + commitment   * 0.20
"""
from __future__ import annotations

from typing import Any, Optional

from ..models.group_state import GroupState, GROUP_STATUS_ACTIVE, GROUP_STATUS_DISSOLVED, ROLE_LEADER, ROLE_MEMBER

# Minimum members to keep a group alive
_MIN_GROUP_MEMBERS = 2

# Hostility threshold that triggers dissolution
_DISSOLUTION_HOSTILITY_THRESHOLD = 0.6


def get_agent_group(
    agent_id: str,
    state: dict[str, Any],
) -> Optional[GroupState]:
    """Return the GroupState this agent belongs to, or None."""
    for group_data in state.get("groups", {}).values():
        if agent_id in group_data.get("members", []):
            return GroupState(**group_data)
    return None


def create_group(
    agent_id: str,
    other_id: str,
    shared_goal: str,
    world_turn: int,
    state: dict[str, Any],
) -> GroupState:
    """Create a new two-member group.

    The agent with the higher leader_score becomes the leader.

    Parameters
    ----------
    agent_id, other_id
        The two agents forming the group.
    shared_goal
        The global goal they share.
    world_turn
        Current world turn.
    state
        Mutable world state.

    Returns
    -------
    GroupState
        The newly created group.
    """
    agents = state.get("agents", {})
    agent = agents.get(agent_id, {})
    other = agents.get(other_id, {})

    # Elect leader based on score
    a_score = _leader_score(agent_id, agent, state)
    b_score = _leader_score(other_id, other, state)
    leader_id = agent_id if a_score >= b_score else other_id

    group_id = f"group_{agent_id}_{world_turn}"
    group = GroupState(
        group_id=group_id,
        leader_id=leader_id,
        members=[agent_id, other_id],
        shared_goal=shared_goal,
        shared_plan=None,
        hierarchy={
            agent_id: ROLE_LEADER if agent_id == leader_id else ROLE_MEMBER,
            other_id: ROLE_LEADER if other_id == leader_id else ROLE_MEMBER,
        },
        status=GROUP_STATUS_ACTIVE,
        formation_turn=world_turn,
    )
    state.setdefault("groups", {})[group_id] = {
        "group_id": group.group_id,
        "leader_id": group.leader_id,
        "members": list(group.members),
        "shared_goal": group.shared_goal,
        "shared_plan": group.shared_plan,
        "hierarchy": dict(group.hierarchy),
        "status": group.status,
        "formation_turn": group.formation_turn,
    }
    return group


def dissolve_group(
    group_id: str,
    state: dict[str, Any],
) -> None:
    """Mark a group as dissolved and remove it from state."""
    groups = state.get("groups", {})
    if group_id in groups:
        groups[group_id]["status"] = GROUP_STATUS_DISSOLVED
        del groups[group_id]


def elect_new_leader(
    group_id: str,
    state: dict[str, Any],
) -> Optional[str]:
    """Elect a new leader after the current one is gone.

    Returns the new leader's agent_id, or None if dissolution is required.
    """
    groups = state.get("groups", {})
    group_data = groups.get(group_id, {})
    agents = state.get("agents", {})
    members: list[str] = [
        mid for mid in group_data.get("members", [])
        if agents.get(mid, {}).get("is_alive", True)
        and not agents.get(mid, {}).get("has_left_zone")
    ]
    if not members:
        dissolve_group(group_id, state)
        return None
    best = max(members, key=lambda mid: _leader_score(mid, agents.get(mid, {}), state))
    group_data["leader_id"] = best
    group_data["hierarchy"][best] = ROLE_LEADER
    return best


def should_dissolve(
    group_id: str,
    state: dict[str, Any],
) -> bool:
    """Return True if the group should be dissolved this tick."""
    groups = state.get("groups", {})
    group_data = groups.get(group_id, {})
    agents = state.get("agents", {})
    members: list[str] = group_data.get("members", [])
    alive_members = [
        mid for mid in members
        if agents.get(mid, {}).get("is_alive", True)
        and not agents.get(mid, {}).get("has_left_zone")
    ]
    if len(alive_members) < _MIN_GROUP_MEMBERS:
        return True

    # Check mutual hostility
    from ..social.relations import get_relation
    for i, mid_a in enumerate(alive_members):
        for mid_b in alive_members[i + 1:]:
            rel_ab = get_relation(mid_a, mid_b, state)
            rel_ba = get_relation(mid_b, mid_a, state)
            if (rel_ab.hostility > _DISSOLUTION_HOSTILITY_THRESHOLD
                    or rel_ba.hostility > _DISSOLUTION_HOSTILITY_THRESHOLD):
                return True
    return False


# ── Private helpers ────────────────────────────────────────────────────────────

def _leader_score(
    agent_id: str,
    agent: dict[str, Any],
    state: dict[str, Any],
) -> float:
    """Compute the leadership suitability score for an agent.

    Score = respect*0.30 + trust*0.15 + competence*0.25
            + health_ratio*0.10 + commitment*0.20
    """
    from ..social.relations import get_relation
    from ..needs import _agent_wealth

    # Trust and respect: average of relations from group members toward this agent
    agents = state.get("agents", {})
    trust_sum = 0.0
    respect_sum = 0.0
    count = 0
    for other_id in agents:
        if other_id == agent_id:
            continue
        rel = get_relation(other_id, agent_id, state)
        trust_sum += rel.trust
        respect_sum += rel.respect
        count += 1
    trust = (trust_sum / count) if count else 0.0
    respect = (respect_sum / count) if count else 0.0

    # Competence: proxy for skill_stalker
    competence = min(1.0, agent.get("skill_stalker", 1) / 5.0)

    # Health ratio
    hp = agent.get("hp", 100)
    health_ratio = hp / 100.0

    # Commitment: whether global goal is not yet achieved
    commitment = 0.0 if agent.get("global_goal_achieved") else 1.0

    return (
        respect    * 0.30
        + trust    * 0.15
        + competence * 0.25
        + health_ratio * 0.10
        + commitment * 0.20
    )
