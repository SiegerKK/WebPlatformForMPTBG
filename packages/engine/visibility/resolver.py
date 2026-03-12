from __future__ import annotations
from typing import Any, Dict, List

class VisibilityResolver:
    """Resolves which entities and events are visible to a participant."""

    def filter_entities(
        self,
        entities: List[Dict[str, Any]],
        participant_id: str,
    ) -> List[Dict[str, Any]]:
        result = []
        for entity in entities:
            scope = entity.get("visibility_scope", "public")
            if scope == "public":
                result.append(entity)
            elif scope == "owner_only":
                if entity.get("owner_participant_id") == participant_id:
                    result.append(entity)
            elif scope == "controller_only":
                if entity.get("controller_participant_id") == participant_id:
                    result.append(entity)
        return result

    def filter_events(
        self,
        events: List[Dict[str, Any]],
        participant_id: str,
    ) -> List[Dict[str, Any]]:
        return [e for e in events if e.get("visibility_scope", "public") == "public"]
