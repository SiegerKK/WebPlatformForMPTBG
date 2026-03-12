from __future__ import annotations
from typing import Any, Dict, List, Optional
import uuid

class ProjectionBuilder:
    """Builds materialized state projections from events."""

    def build_summary(self, match_id: uuid.UUID, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Build a high-level match summary projection."""
        return {
            "match_id": str(match_id),
            "event_count": len(events),
            "projection_type": "summary",
        }

    def build_player_visible(
        self,
        context_state: Dict[str, Any],
        entities: List[Dict[str, Any]],
        participant_id: str,
    ) -> Dict[str, Any]:
        """Build a player-visible projection filtering by visibility scope."""
        visible = [
            e for e in entities
            if e.get("visibility_scope", "public") == "public"
            or e.get("owner_participant_id") == participant_id
        ]
        return {
            "projection_type": "player_visible",
            "participant_id": participant_id,
            "state": context_state,
            "entities": visible,
        }

    def build_event_feed(
        self,
        events: List[Dict[str, Any]],
        participant_id: str,
    ) -> List[Dict[str, Any]]:
        """Build a filtered event feed for a participant."""
        return [
            e for e in events
            if e.get("visibility_scope", "public") == "public"
        ]
