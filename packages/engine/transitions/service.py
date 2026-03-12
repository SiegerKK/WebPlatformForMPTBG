from __future__ import annotations
from typing import Any, Dict, List, Optional
from packages.core.enums.context import ContextStatus
from packages.core.enums.match import MatchStatus

class TransitionService:
    """Handles state machine transitions for matches and contexts."""

    MATCH_TRANSITIONS: Dict[MatchStatus, List[MatchStatus]] = {
        MatchStatus.DRAFT: [MatchStatus.WAITING_FOR_PLAYERS, MatchStatus.ARCHIVED],
        MatchStatus.WAITING_FOR_PLAYERS: [MatchStatus.INITIALIZING, MatchStatus.ARCHIVED],
        MatchStatus.INITIALIZING: [MatchStatus.ACTIVE, MatchStatus.FAILED],
        MatchStatus.ACTIVE: [MatchStatus.PAUSED, MatchStatus.FINISHED, MatchStatus.FAILED],
        MatchStatus.PAUSED: [MatchStatus.ACTIVE, MatchStatus.ARCHIVED],
        MatchStatus.FINISHED: [MatchStatus.ARCHIVED],
        MatchStatus.FAILED: [MatchStatus.ARCHIVED],
        MatchStatus.ARCHIVED: [],
    }

    CONTEXT_TRANSITIONS: Dict[ContextStatus, List[ContextStatus]] = {
        ContextStatus.CREATED: [ContextStatus.INITIALIZING, ContextStatus.ACTIVE],
        ContextStatus.INITIALIZING: [ContextStatus.ACTIVE, ContextStatus.FAILED],
        ContextStatus.ACTIVE: [ContextStatus.RESOLVING, ContextStatus.SUSPENDED, ContextStatus.FAILED],
        ContextStatus.RESOLVING: [ContextStatus.FINISHED, ContextStatus.FAILED],
        ContextStatus.SUSPENDED: [ContextStatus.ACTIVE, ContextStatus.ARCHIVED],
        ContextStatus.FINISHED: [ContextStatus.ARCHIVED],
        ContextStatus.FAILED: [ContextStatus.ARCHIVED],
        ContextStatus.ARCHIVED: [],
    }

    def can_transition_match(self, from_status: MatchStatus, to_status: MatchStatus) -> bool:
        return to_status in self.MATCH_TRANSITIONS.get(from_status, [])

    def can_transition_context(self, from_status: ContextStatus, to_status: ContextStatus) -> bool:
        return to_status in self.CONTEXT_TRANSITIONS.get(from_status, [])
