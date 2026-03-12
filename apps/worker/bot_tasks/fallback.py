"""
Bot fallback task: executes a bot policy on behalf of a timed-out human player.
"""
from __future__ import annotations
from typing import Any, Dict, Optional

class BotFallbackExecutor:
    """
    Executes a bot policy to generate a command on behalf of a participant
    who failed to submit before the turn deadline.
    """

    def execute(
        self,
        match_id: str,
        context_id: str,
        participant_id: str,
        policy_id: Optional[str],
        game_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Run the fallback policy and return a command envelope.
        Falls back to 'end_turn' if no policy is configured.
        """
        if policy_id is None:
            return {
                "match_id": match_id,
                "context_id": context_id,
                "command_type": "end_turn",
                "payload": {"reason": "timeout_fallback"},
            }
        # TODO: load and execute the bot policy
        return {
            "match_id": match_id,
            "context_id": context_id,
            "command_type": "end_turn",
            "payload": {"reason": "bot_policy_fallback", "policy_id": policy_id},
        }
