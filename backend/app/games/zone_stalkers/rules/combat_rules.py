"""
Rules for the encounter_combat context.

Supported commands:
- attack(target_id)
- use_item(item_id)
- retreat
- end_turn
"""
from typing import List, Tuple, Dict, Any
from sdk.rule_set import RuleCheckResult


def validate_combat_command(
    command_type: str,
    payload: Dict[str, Any],
    state: Dict[str, Any],
    player_id: str,
) -> RuleCheckResult:
    if state.get("combat_over"):
        return RuleCheckResult(valid=False, error="Combat is already over")

    participants = state.get("participants", {})
    agent_id = _get_player_agent(state, player_id)
    if agent_id is None:
        return RuleCheckResult(valid=False, error="No combat participant for this player")

    agent = participants.get(agent_id)
    if not agent or not agent.get("is_alive", True):
        return RuleCheckResult(valid=False, error="Your agent is not in combat or is dead")

    # Turn check: only active participant can act
    active_agent_id = state.get("active_agent_id")
    if active_agent_id and active_agent_id != agent_id:
        return RuleCheckResult(valid=False, error="It is not your turn in combat")

    if command_type == "end_turn":
        return RuleCheckResult(valid=True)

    if command_type == "retreat":
        return RuleCheckResult(valid=True)

    if command_type == "attack":
        target_id = payload.get("target_id")
        if not target_id:
            return RuleCheckResult(valid=False, error="target_id is required")
        target = participants.get(target_id)
        if not target:
            return RuleCheckResult(valid=False, error="Target not found in combat")
        if not target.get("is_alive", True):
            return RuleCheckResult(valid=False, error="Target is already dead")
        if target.get("side") == agent.get("side"):
            return RuleCheckResult(valid=False, error="Cannot attack your own side")
        return RuleCheckResult(valid=True)

    if command_type == "use_item":
        item_id = payload.get("item_id")
        if not item_id:
            return RuleCheckResult(valid=False, error="item_id is required")
        inventory = agent.get("inventory", [])
        if not any(i["id"] == item_id for i in inventory):
            return RuleCheckResult(valid=False, error="Item not in inventory")
        return RuleCheckResult(valid=True)

    return RuleCheckResult(valid=False, error=f"Unknown combat command: {command_type}")


def resolve_combat_command(
    command_type: str,
    payload: Dict[str, Any],
    state: Dict[str, Any],
    player_id: str,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    import copy
    state = copy.deepcopy(state)
    events: List[Dict[str, Any]] = []
    participants = state.get("participants", {})
    agent_id = _get_player_agent(state, player_id)

    if command_type == "end_turn":
        events.append({"event_type": "turn_submitted", "payload": {"participant_id": player_id}})
        state = _advance_combat_turn(state, events)
        return state, events

    if command_type == "retreat":
        if agent_id and agent_id in participants:
            participants[agent_id]["retreated"] = True
            participants[agent_id]["is_alive"] = False  # out of combat
            events.append({"event_type": "agent_retreated", "payload": {"agent_id": agent_id}})
        state = _check_combat_over(state, events)
        state = _advance_combat_turn(state, events)
        return state, events

    agent = participants.get(agent_id, {})

    if command_type == "attack":
        target_id = payload["target_id"]
        target = participants[target_id]

        # Calculate damage
        weapon_damage = _get_weapon_damage(agent)
        defense = target.get("defense", 0)
        net_damage = max(1, weapon_damage - defense)
        target["hp"] = max(0, target["hp"] - net_damage)

        events.append({
            "event_type": "attack_resolved",
            "payload": {
                "attacker_id": agent_id,
                "target_id": target_id,
                "damage": net_damage,
                "hp_remaining": target["hp"],
            },
        })

        if target["hp"] <= 0:
            target["is_alive"] = False
            loot = _generate_loot_from_participant(target)
            events.append({
                "event_type": "participant_killed",
                "payload": {
                    "killed_id": target_id,
                    "killer_id": agent_id,
                    "loot": loot,
                },
            })
            # Add loot to attacker's inventory
            agent.setdefault("inventory", []).extend(loot)
            money_drop = target.get("money_drop", 0)
            if money_drop > 0:
                agent["money"] = agent.get("money", 0) + money_drop

        state = _check_combat_over(state, events)
        state = _advance_combat_turn(state, events)

    elif command_type == "use_item":
        item_id = payload["item_id"]
        inventory = agent.get("inventory", [])
        item = next((i for i in inventory if i["id"] == item_id), None)
        if item:
            from app.games.zone_stalkers.balance.items import ITEM_TYPES
            item_info = ITEM_TYPES.get(item["type"], {})
            effects = item_info.get("effects", {})
            for stat, delta in effects.items():
                if stat == "hp":
                    agent["hp"] = min(agent.get("max_hp", 100), agent.get("hp", 100) + delta)
                elif stat == "radiation":
                    agent["radiation"] = max(0, agent.get("radiation", 0) + delta)
            # Remove item from inventory
            agent["inventory"] = [i for i in inventory if i["id"] != item_id]
            events.append({
                "event_type": "item_used",
                "payload": {"agent_id": agent_id, "item_id": item_id, "item_type": item["type"]},
            })
        state = _advance_combat_turn(state, events)

    return state, events


# ──────────────────────────────
# Private helpers
# ──────────────────────────────

def _get_player_agent(state: Dict[str, Any], player_id: str) -> str | None:
    return state.get("player_agents", {}).get(player_id)


def _get_weapon_damage(agent: Dict[str, Any]) -> int:
    equipment = agent.get("equipment", {})
    weapon = equipment.get("weapon")
    if weapon:
        from app.games.zone_stalkers.balance.items import ITEM_TYPES
        weapon_info = ITEM_TYPES.get(weapon.get("type", ""), {})
        return weapon_info.get("damage", 15)
    return 10  # bare hands


def _generate_loot_from_participant(participant: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return a subset of the participant's inventory as loot."""
    inventory = participant.get("inventory", [])
    # Return all inventory items as loot
    return list(inventory)


def _advance_combat_turn(state: Dict[str, Any], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Advance the initiative order to the next living participant."""
    if state.get("combat_over"):
        return state
    order = state.get("initiative_order", [])
    if not order:
        return state
    participants = state.get("participants", {})
    current = state.get("active_agent_id")
    try:
        idx = order.index(current) if current in order else -1
    except ValueError:
        idx = -1

    # Find next alive participant
    for i in range(1, len(order) + 1):
        next_idx = (idx + i) % len(order)
        next_id = order[next_idx]
        p = participants.get(next_id, {})
        if p.get("is_alive", True) and not p.get("retreated", False):
            state["active_agent_id"] = next_id
            # Increment turn number when we complete a full round
            if next_idx == 0:
                state["turn_number"] = state.get("turn_number", 1) + 1
                events.append({
                    "event_type": "combat_round_advanced",
                    "payload": {"turn_number": state["turn_number"]},
                })
            return state

    # No living participant found
    state["combat_over"] = True
    return state


def _check_combat_over(state: Dict[str, Any], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Check if one side has been eliminated."""
    if state.get("combat_over"):
        return state
    participants = state.get("participants", {})

    sides: Dict[str, bool] = {}
    for p in participants.values():
        side = p.get("side", "unknown")
        alive = p.get("is_alive", True) and not p.get("retreated", False)
        if side not in sides:
            sides[side] = False
        sides[side] = sides[side] or alive

    active_sides = [s for s, alive in sides.items() if alive]
    if len(active_sides) <= 1:
        state["combat_over"] = True
        winner_side = active_sides[0] if active_sides else None
        state["winner_side"] = winner_side
        # Check if turn limit reached
        max_turns = state.get("max_turns", 20)
        if state.get("turn_number", 1) >= max_turns:
            state["combat_over"] = True
            state["winner_side"] = None  # draw
            events.append({"event_type": "combat_ended", "payload": {"reason": "turn_limit", "winner_side": None}})
        else:
            events.append({
                "event_type": "combat_ended",
                "payload": {"reason": "elimination", "winner_side": winner_side},
            })
    return state
