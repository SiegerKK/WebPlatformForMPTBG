from abc import ABC, abstractmethod
from typing import Any, List, Tuple, Optional
from pydantic import BaseModel

class RuleCheckResult(BaseModel):
    valid: bool
    error: Optional[str] = None
    events: List[dict] = []

class RuleSet(ABC):
    @abstractmethod
    def validate_command(
        self,
        command_type: str,
        payload: dict,
        context_state: dict,
        entities: List[dict],
        player_id: str,
    ) -> RuleCheckResult: ...

    @abstractmethod
    def resolve_command(
        self,
        command_type: str,
        payload: dict,
        context_state: dict,
        entities: List[dict],
        player_id: str,
    ) -> Tuple[dict, List[dict]]: ...

    # ── Optional extension hooks ──────────────────────────────────────────────

    def create_initial_context_state(
        self,
        context_type: str,
        match_id: Any,
        db: Any,
    ) -> Optional[dict]:
        """
        Return an auto-generated initial state_blob for *context_type*, or
        ``None`` if no auto-initialisation is needed.

        Called by the context creation endpoint when state_blob is not supplied
        by the client.  The default implementation returns ``None`` (no
        auto-init) so existing rulesets do not need to be updated.

        :param context_type: The ``context_type`` string from the request.
        :param match_id:     UUID of the parent match.
        :param db:           Active SQLAlchemy ``Session``.
        """
        return None

    def tick(self, match_id: str, db: Any) -> dict:
        """
        Advance the game by one tick for the given match.

        Called by the platform ticker (both the periodic background task and
        the manual ``POST /api/matches/{id}/tick`` endpoint).  The default
        implementation returns an error dict so games that do not need a
        periodic tick are not accidentally ticked.

        :param match_id: String representation of the match UUID.
        :param db:       Active SQLAlchemy ``Session``.
        :returns:        A summary dict; must contain ``"error"`` key on
                         failure.
        """
        return {"error": f"game does not support tick"}
