"""
Main RuleSet for Zone Stalkers.

Dispatches commands to the appropriate sub-ruleset based on context_type
stored in the state_blob.
"""
from typing import List, Tuple
from sdk.rule_set import RuleSet, RuleCheckResult

from app.games.zone_stalkers.rules.world_rules import (
    validate_world_command,
    resolve_world_command,
)
from app.games.zone_stalkers.rules.combat_rules import (
    validate_combat_command,
    resolve_combat_command,
)
from app.games.zone_stalkers.rules.trade_rules import (
    validate_trade_command,
    resolve_trade_command,
)
from app.games.zone_stalkers.rules.exploration_rules import (
    validate_exploration_command,
    resolve_exploration_command,
)
from app.games.zone_stalkers.rules.event_rules import (
    validate_event_command,
    resolve_event_command,
)


class ZoneStalkerRuleSet(RuleSet):
    """Dispatches to context-specific rule modules."""

    def validate_command(
        self,
        command_type: str,
        payload: dict,
        context_state: dict,
        entities: List[dict],
        player_id: str,
    ) -> RuleCheckResult:
        ctx_type = (context_state or {}).get("context_type", "zone_map")
        state = context_state or {}

        if ctx_type == "zone_map":
            return validate_world_command(command_type, payload, state, player_id)
        if ctx_type == "encounter_combat":
            return validate_combat_command(command_type, payload, state, player_id)
        if ctx_type == "trade_session":
            return validate_trade_command(command_type, payload, state, player_id)
        if ctx_type == "location_exploration":
            return validate_exploration_command(command_type, payload, state, player_id)
        if ctx_type == "zone_event":
            return validate_event_command(command_type, payload, state, player_id)

        # Fallback: always allow end_turn
        if command_type == "end_turn":
            return RuleCheckResult(valid=True)
        return RuleCheckResult(valid=False, error=f"Unknown context type: {ctx_type}")

    def resolve_command(
        self,
        command_type: str,
        payload: dict,
        context_state: dict,
        entities: List[dict],
        player_id: str,
    ) -> Tuple[dict, List[dict]]:
        ctx_type = (context_state or {}).get("context_type", "zone_map")
        state = context_state or {}

        if ctx_type == "zone_map":
            return resolve_world_command(command_type, payload, state, player_id)
        if ctx_type == "encounter_combat":
            return resolve_combat_command(command_type, payload, state, player_id)
        if ctx_type == "trade_session":
            return resolve_trade_command(command_type, payload, state, player_id)
        if ctx_type == "location_exploration":
            return resolve_exploration_command(command_type, payload, state, player_id)
        if ctx_type == "zone_event":
            return resolve_event_command(command_type, payload, state, player_id)

        # Fallback
        return state, [{"event_type": f"{command_type}_executed", "payload": payload}]
