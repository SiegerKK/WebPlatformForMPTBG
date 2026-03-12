"""
Temporal activity: handles turn timeout by applying fallback policies.
"""
from __future__ import annotations
from typing import Any, Dict

async def handle_turn_timeout(context_id: str, turn_number: int) -> Dict[str, Any]:
    """
    Called when a turn window expires.
    Applies the configured fallback policy for participants who haven't submitted.
    """
    # TODO: load context, find non-submitted participants, apply fallback
    return {
        "context_id": context_id,
        "turn_number": turn_number,
        "handled": True,
    }
