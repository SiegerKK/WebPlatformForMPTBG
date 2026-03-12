import copy
from typing import Any, Dict, List, Tuple, Optional
from packages.core.contracts.rule_resolver import RuleCheckResult
from packages.games.demo_sector.balance import (
    GROUP_MOVE_RANGE, STRATEGIC_MAP_WIDTH, STRATEGIC_MAP_HEIGHT,
    UNIT_MOVE_RANGE, UNIT_ATTACK_RANGE, UNIT_DAMAGE, COVER_DAMAGE_REDUCTION,
)


def _ok() -> RuleCheckResult:
    return RuleCheckResult(valid=True)


def _fail(code: str, msg: str) -> RuleCheckResult:
    return RuleCheckResult(valid=False, error_code=code, error_message=msg)


def _manhattan(a: dict, b: dict) -> int:
    return abs(a["x"] - b["x"]) + abs(a["y"] - b["y"])


class DemoSectorRuleResolver:
    def __init__(self, context_type: str = "sector_map"):
        self.context_type = context_type

    def validate(self, command_type, payload, context_state, entities, participant_id):
        handler = getattr(self, f"_validate_{command_type}", None)
        if handler:
            return handler(payload, context_state, entities, participant_id)
        return _ok()

    def resolve(self, command_type, payload, state, entities, participant_id):
        handler = getattr(self, f"_resolve_{command_type}", None)
        if handler:
            return handler(payload, state, entities, participant_id)
        return state, []

    def _validate_move_group(self, payload, context_state, entities, participant_id):
        group_id = payload.get("group_id")
        target = payload.get("target_position")
        if not group_id or not target:
            return _fail("MISSING_PAYLOAD", "group_id and target_position required")
        groups = context_state.get("groups", {})
        group = groups.get(group_id)
        if not group:
            return _fail("GROUP_NOT_FOUND", f"Group {group_id} not found")
        if not group.get("alive", True):
            return _fail("GROUP_DEAD", "Group is dead")
        if str(group.get("owner_participant_id")) != str(participant_id):
            return _fail("NOT_OWNER", "You do not own this group")
        pos = group.get("position", {})
        dist = _manhattan(pos, target)
        if dist > GROUP_MOVE_RANGE:
            return _fail("OUT_OF_RANGE", f"Distance {dist} exceeds move range {GROUP_MOVE_RANGE}")
        if not (0 <= target["x"] < STRATEGIC_MAP_WIDTH and 0 <= target["y"] < STRATEGIC_MAP_HEIGHT):
            return _fail("OUT_OF_BOUNDS", "Target position is out of bounds")
        return _ok()

    def _resolve_move_group(self, payload, context_state, entities, participant_id):
        group_id = payload["group_id"]
        target = payload["target_position"]
        groups = copy.deepcopy(context_state.get("groups", {}))
        group = groups[group_id]
        old_pos = dict(group["position"])
        group["position"] = target
        groups[group_id] = group
        events = [{"event_type": "GroupMoved", "payload": {"group_id": group_id, "from": old_pos, "to": target}}]
        for gid, g in groups.items():
            if gid == group_id:
                continue
            if not g.get("alive", True):
                continue
            if g.get("side") == group.get("side"):
                continue
            if g["position"]["x"] == target["x"] and g["position"]["y"] == target["y"]:
                events.append({"event_type": "BattleTriggered", "payload": {"attacker_group_id": group_id, "defender_group_id": gid, "position": target}})
                break
        new_state = {**context_state, "groups": groups}
        return new_state, events

    def _validate_end_turn(self, payload, context_state, entities, participant_id):
        return _ok()

    def _resolve_end_turn(self, payload, context_state, entities, participant_id):
        new_state = {**context_state, "turn": context_state.get("turn", 1) + 1}
        events = [{"event_type": "TurnEnded", "payload": {"participant_id": participant_id}}]
        return new_state, events

    def _validate_move_unit(self, payload, context_state, entities, participant_id):
        unit_id = payload.get("unit_id")
        target = payload.get("target_position")
        if not unit_id or not target:
            return _fail("MISSING_PAYLOAD", "unit_id and target_position required")
        units = context_state.get("units", {})
        unit = units.get(unit_id)
        if not unit:
            return _fail("UNIT_NOT_FOUND", f"Unit {unit_id} not found")
        if not unit.get("alive", True):
            return _fail("UNIT_DEAD", "Unit is dead")
        if str(unit.get("owner_participant_id")) != str(participant_id):
            return _fail("NOT_OWNER", "You do not own this unit")
        if unit.get("has_moved", False):
            return _fail("ALREADY_MOVED", "Unit has already moved this turn")
        dist = _manhattan(unit["position"], target)
        if dist > UNIT_MOVE_RANGE:
            return _fail("OUT_OF_RANGE", f"Distance {dist} exceeds move range {UNIT_MOVE_RANGE}")
        for obs in context_state.get("obstacles", []):
            if obs["x"] == target["x"] and obs["y"] == target["y"]:
                return _fail("BLOCKED", "Target position is blocked by obstacle")
        return _ok()

    def _resolve_move_unit(self, payload, context_state, entities, participant_id):
        unit_id = payload["unit_id"]
        target = payload["target_position"]
        units = copy.deepcopy(context_state.get("units", {}))
        unit = units[unit_id]
        old_pos = dict(unit["position"])
        unit["position"] = target
        unit["has_moved"] = True
        units[unit_id] = unit
        events = [{"event_type": "UnitMoved", "payload": {"unit_id": unit_id, "from": old_pos, "to": target}}]
        new_state = {**context_state, "units": units}
        return new_state, events

    def _validate_attack_unit(self, payload, context_state, entities, participant_id):
        attacker_id = payload.get("attacker_id")
        target_id = payload.get("target_id")
        if not attacker_id or not target_id:
            return _fail("MISSING_PAYLOAD", "attacker_id and target_id required")
        units = context_state.get("units", {})
        attacker = units.get(attacker_id)
        target = units.get(target_id)
        if not attacker:
            return _fail("UNIT_NOT_FOUND", f"Attacker {attacker_id} not found")
        if not target:
            return _fail("UNIT_NOT_FOUND", f"Target {target_id} not found")
        if not attacker.get("alive", True):
            return _fail("UNIT_DEAD", "Attacker is dead")
        if not target.get("alive", True):
            return _fail("TARGET_DEAD", "Target is already dead")
        if str(attacker.get("owner_participant_id")) != str(participant_id):
            return _fail("NOT_OWNER", "You do not own this unit")
        if attacker.get("has_attacked", False):
            return _fail("ALREADY_ATTACKED", "Unit has already attacked this turn")
        if attacker.get("side") == target.get("side"):
            return _fail("FRIENDLY_FIRE", "Cannot attack friendly unit")
        dist = _manhattan(attacker["position"], target["position"])
        if dist > UNIT_ATTACK_RANGE:
            return _fail("OUT_OF_RANGE", f"Distance {dist} exceeds attack range {UNIT_ATTACK_RANGE}")
        return _ok()

    def _resolve_attack_unit(self, payload, context_state, entities, participant_id):
        attacker_id = payload["attacker_id"]
        target_id = payload["target_id"]
        units = copy.deepcopy(context_state.get("units", {}))
        attacker = units[attacker_id]
        target = units[target_id]
        damage = UNIT_DAMAGE
        cover = context_state.get("cover", [])
        tp = target["position"]
        in_cover = any(c["x"] == tp["x"] and c["y"] == tp["y"] for c in cover)
        if in_cover:
            damage = max(0, damage - COVER_DAMAGE_REDUCTION)
        target["hp"] = target.get("hp", 10) - damage
        attacker["has_attacked"] = True
        events = [
            {"event_type": "AttackResolved", "payload": {"attacker_id": attacker_id, "target_id": target_id, "damage": damage, "in_cover": in_cover}},
            {"event_type": "DamageApplied", "payload": {"unit_id": target_id, "damage": damage, "remaining_hp": target["hp"]}},
        ]
        if target["hp"] <= 0:
            target["alive"] = False
            events.append({"event_type": "UnitDestroyed", "payload": {"unit_id": target_id}})
        units[attacker_id] = attacker
        units[target_id] = target
        new_state = {**context_state, "units": units}
        sides = {}
        for uid, u in new_state["units"].items():
            side = u.get("side")
            if side not in sides:
                sides[side] = []
            if u.get("alive", True):
                sides[side].append(uid)
        alive_sides = [s for s, alive in sides.items() if alive]
        if len(alive_sides) <= 1:
            winner = alive_sides[0] if alive_sides else None
            events.append({"event_type": "BattleEnded", "payload": {"winner_side": winner}})
        return new_state, events

    def _validate_retreat(self, payload, context_state, entities, participant_id):
        return _ok()

    def _resolve_retreat(self, payload, context_state, entities, participant_id):
        group_id = payload.get("group_id", "unknown")
        new_state = {**context_state, "retreated": True}
        events = [{"event_type": "BattleEnded", "payload": {"winner_side": None, "retreated_group_id": group_id}}]
        return new_state, events
