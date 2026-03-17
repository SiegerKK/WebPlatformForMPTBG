"""dialogue — DialogueSession model and MVP dialogue operations (Phase 6 stub).

A DialogueSession is a short structured exchange between two agents.
It is processed by a session manager similar to combat_interactions.

Lifecycle:
    created → active → resolved | interrupted | aborted

MVP dialogue operations:
    ask_for_intel       — request known location of target/item
    exchange_memories   — share a memory observation
    offer_trade         — propose a trade transaction
    propose_grouping    — propose forming a group
    warn_about_threat   — share hazard/emission/enemy info
    ask_for_help        — request healing/resupply

Phase 6 note: this module contains data structures only.
Actual session processing will be added in Phase 6.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# ── Dialogue operation kinds ──────────────────────────────────────────────────
DIALOGUE_ASK_INTEL = "ask_for_intel"
DIALOGUE_EXCHANGE_MEMORIES = "exchange_memories"
DIALOGUE_OFFER_TRADE = "offer_trade"
DIALOGUE_PROPOSE_GROUP = "propose_grouping"
DIALOGUE_WARN_THREAT = "warn_about_threat"
DIALOGUE_ASK_HELP = "ask_for_help"

# ── Session statuses ──────────────────────────────────────────────────────────
SESSION_CREATED = "created"
SESSION_ACTIVE = "active"
SESSION_RESOLVED = "resolved"
SESSION_INTERRUPTED = "interrupted"
SESSION_ABORTED = "aborted"

# ── Duration in ticks per operation ──────────────────────────────────────────
DIALOGUE_DURATION: dict[str, int] = {
    DIALOGUE_ASK_INTEL:         1,
    DIALOGUE_EXCHANGE_MEMORIES: 2,
    DIALOGUE_OFFER_TRADE:       2,
    DIALOGUE_PROPOSE_GROUP:     3,
    DIALOGUE_WARN_THREAT:       1,
    DIALOGUE_ASK_HELP:          1,
}


@dataclass
class DialogueSession:
    """A short structured exchange between exactly two agents.

    Parameters
    ----------
    session_id
        Unique identifier.
    initiator_id
        Agent that started the dialogue.
    responder_id
        Agent that was addressed.
    topic
        One of the DIALOGUE_* constants.
    participants
        List of both participant agent IDs.
    offers
        Structured offer data (depends on topic).
    shared_memories
        Memory entries proposed for exchange.
    relation_changes
        Pending relation updates to apply on resolution.
    result
        Outcome description written on resolution.
    status
        Current lifecycle status.
    created_turn
        World turn when the session was initiated.
    ticks_remaining
        How many ticks until the session resolves.
    """

    session_id: str
    initiator_id: str
    responder_id: str
    topic: str
    participants: list[str] = field(default_factory=list)
    offers: dict[str, Any] = field(default_factory=dict)
    shared_memories: list[dict[str, Any]] = field(default_factory=list)
    relation_changes: dict[str, Any] = field(default_factory=dict)
    result: Optional[str] = None
    status: str = SESSION_CREATED
    created_turn: Optional[int] = None
    ticks_remaining: int = 1
