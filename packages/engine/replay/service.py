from __future__ import annotations
from typing import Any, Dict, List, Optional
import uuid

class ReplayService:
    """Reconstructs match state by replaying events from a snapshot."""

    def replay_from_events(
        self,
        initial_state: Dict[str, Any],
        events: List[Dict[str, Any]],
        up_to_sequence: Optional[int] = None,
    ) -> Dict[str, Any]:
        state = dict(initial_state)
        for event in events:
            seq = event.get("sequence_no", 0)
            if up_to_sequence is not None and seq > up_to_sequence:
                break
            state = self._apply_event(state, event)
        return state

    def _apply_event(self, state: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
        # Default: merge event payload into state
        new_state = dict(state)
        payload = event.get("payload", {})
        new_state.update(payload)
        return new_state

    def build_snapshot(
        self,
        match_id: uuid.UUID,
        context_id: uuid.UUID,
        state: Dict[str, Any],
        up_to_sequence: int,
    ) -> Dict[str, Any]:
        return {
            "match_id": str(match_id),
            "context_id": str(context_id),
            "event_sequence_up_to": up_to_sequence,
            "payload": state,
        }
