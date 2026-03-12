from __future__ import annotations
from typing import Any, Dict, List, Optional
import uuid
from packages.core.enums.turn import TurnMode, TurnPhase, TurnStatus

class TurnEngine:
    """Manages turn lifecycle: open, collect, resolve, advance."""

    def open_turn(self, turn_number: int, mode: TurnMode) -> Dict[str, Any]:
        return {
            "turn_number": turn_number,
            "mode": mode,
            "phase": TurnPhase.COLLECTING,
            "status": TurnStatus.WAITING_FOR_PLAYERS,
            "submitted_players": [],
        }

    def submit_player(self, turn_state: Dict[str, Any], player_id: str) -> Dict[str, Any]:
        submitted = list(turn_state.get("submitted_players", []))
        if player_id not in submitted:
            submitted.append(player_id)
        return {**turn_state, "submitted_players": submitted}

    def can_resolve(self, turn_state: Dict[str, Any], total_players: int) -> bool:
        if turn_state.get("mode") == TurnMode.STRICT:
            active_side = turn_state.get("active_side_id")
            return active_side in turn_state.get("submitted_players", [])
        return len(turn_state.get("submitted_players", [])) >= total_players

    def resolve(self, turn_state: Dict[str, Any]) -> Dict[str, Any]:
        return {
            **turn_state,
            "phase": TurnPhase.CLOSED,
            "status": TurnStatus.RESOLVED,
        }
