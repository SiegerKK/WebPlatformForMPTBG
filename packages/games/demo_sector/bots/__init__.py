from typing import Any, Dict, List, Optional


def _manhattan(a: dict, b: dict) -> int:
    return abs(a["x"] - b["x"]) + abs(a["y"] - b["y"])


def _nearest(pos: dict, targets: List[dict]) -> Optional[dict]:
    if not targets:
        return None
    return min(targets, key=lambda t: _manhattan(pos, t))


def _step_toward(pos: dict, target: dict) -> dict:
    dx = target["x"] - pos["x"]
    dy = target["y"] - pos["y"]
    if abs(dx) >= abs(dy):
        return {"x": pos["x"] + (1 if dx > 0 else -1), "y": pos["y"]}
    return {"x": pos["x"], "y": pos["y"] + (1 if dy > 0 else -1)}


class StrategicBot:
    def choose_action(self, context_state: Dict[str, Any], participant_id: str, side_id: str) -> Dict[str, Any]:
        groups = context_state.get("groups", {})
        resource_nodes = context_state.get("resource_nodes", {})
        my_groups = [g for g in groups.values() if g.get("side") == side_id and g.get("alive", True)]
        enemy_groups = [g for g in groups.values() if g.get("side") != side_id and g.get("alive", True)]
        for group in my_groups:
            pos = group["position"]
            group_id = group["id"]
            if enemy_groups:
                nearest_enemy = _nearest(pos, [e["position"] for e in enemy_groups])
                if nearest_enemy and _manhattan(pos, nearest_enemy) > 1:
                    step = _step_toward(pos, nearest_enemy)
                    return {"command_type": "move_group", "payload": {"group_id": group_id, "target_position": step}}
            resource_positions = [rn["position"] for rn in resource_nodes.values() if rn.get("controller") != side_id]
            if resource_positions:
                nearest_res = _nearest(pos, resource_positions)
                if nearest_res and _manhattan(pos, nearest_res) > 0:
                    step = _step_toward(pos, nearest_res)
                    return {"command_type": "move_group", "payload": {"group_id": group_id, "target_position": step}}
        return {"command_type": "end_turn", "payload": {}}


class TacticalBot:
    def choose_action(self, context_state: Dict[str, Any], participant_id: str, side_id: str) -> Dict[str, Any]:
        from packages.games.demo_sector.balance import UNIT_ATTACK_RANGE, UNIT_MOVE_RANGE
        units = context_state.get("units", {})
        my_units = [u for u in units.values() if u.get("side") == side_id and u.get("alive", True)]
        enemy_units = [u for u in units.values() if u.get("side") != side_id and u.get("alive", True)]
        for unit in my_units:
            uid = unit["id"]
            pos = unit["position"]
            if not unit.get("has_attacked", False):
                enemies_in_range = [e for e in enemy_units if _manhattan(pos, e["position"]) <= UNIT_ATTACK_RANGE]
                if enemies_in_range:
                    target = min(enemies_in_range, key=lambda e: e.get("hp", 10))
                    return {"command_type": "attack_unit", "payload": {"attacker_id": uid, "target_id": target["id"]}}
            if not unit.get("has_moved", False) and enemy_units:
                nearest_enemy_pos = _nearest(pos, [e["position"] for e in enemy_units])
                if nearest_enemy_pos and _manhattan(pos, nearest_enemy_pos) > 1:
                    step = _step_toward(pos, nearest_enemy_pos)
                    return {"command_type": "move_unit", "payload": {"unit_id": uid, "target_position": step}}
        return {"command_type": "end_turn", "payload": {}}
