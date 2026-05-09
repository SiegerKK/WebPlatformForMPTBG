"""Tests for _pre_decision_equipment_maintenance and v2_context recording."""
from __future__ import annotations

import pytest

from tests.decision.conftest import make_agent, make_minimal_state


def _make_item(item_type: str, item_id: str = "itm1", value: int = 100) -> dict:
    return {"id": item_id, "type": item_type, "name": item_type, "value": value}



def _ensure_inventory_ids(agent: dict) -> dict:
    """Ensure every inventory item has an 'id' field (conftest ammo lacks one)."""
    for i, item in enumerate(agent.get("inventory", [])):
        if "id" not in item:
            item["id"] = f"auto_{i}"
    return agent

def _run_maintenance(agent_id, agent, state, world_turn=100):
    from app.games.zone_stalkers.rules.tick_rules import _pre_decision_equipment_maintenance
    return _pre_decision_equipment_maintenance(agent_id, agent, state, world_turn)


class TestPreDecisionEquipmentMaintenance:
    """_pre_decision_equipment_maintenance returns events when it acts, None when idle."""

    def test_equip_weapon_from_inventory(self):
        """Weapon in inventory and no weapon equipped → equips it, returns events."""
        agent = make_agent(has_weapon=False, has_armor=True, has_ammo=False)
        weapon = _make_item("pistol", "w1")
        agent["inventory"].append(weapon)
        state = make_minimal_state(agent=agent)

        result = _run_maintenance("bot1", agent, state)

        assert result is not None, "should take action (equip weapon)"
        assert len(result) > 0
        assert agent["equipment"].get("weapon") is not None
        assert agent["equipment"]["weapon"]["type"] == "pistol"

    def test_equip_armor_from_inventory(self):
        """Armor in inventory and no armor equipped → equips it, returns events."""
        agent = make_agent(has_weapon=True, has_armor=False, has_ammo=True)
        _ensure_inventory_ids(agent)
        armor = _make_item("leather_jacket", "a1")
        agent["inventory"].append(armor)
        state = make_minimal_state(agent=agent)

        result = _run_maintenance("bot1", agent, state)

        assert result is not None, "should take action (equip armor)"
        assert agent["equipment"].get("armor") is not None
        assert agent["equipment"]["armor"]["type"] == "leather_jacket"

    def test_pickup_weapon_from_ground(self):
        """No weapon in equipment or inventory, but weapon on ground → picks it up."""
        agent = make_agent(has_weapon=False, has_armor=True, has_ammo=False)
        state = make_minimal_state(agent=agent)
        weapon_on_ground = _make_item("pistol", "w2")
        state["locations"]["loc_a"]["items"] = [weapon_on_ground]

        result = _run_maintenance("bot1", agent, state)

        assert result is not None, "should pick up weapon from ground"
        assert any(i["type"] == "pistol" for i in agent["inventory"])

    def test_no_action_needed_returns_none(self):
        """Agent fully equipped with weapon, armor, ammo → returns None (run pipeline)."""
        agent = make_agent(has_weapon=True, has_armor=True, has_ammo=True)
        # Ensure no heal items and no items on ground to keep it truly idle
        agent["inventory"] = [i for i in agent["inventory"] if i["type"] != "ammo_9mm"]
        agent["inventory"].append(_make_item("ammo_9mm", "amm1", 50))
        state = make_minimal_state(agent=agent)
        # Add a heal item so step-8 doesn't trigger
        from app.games.zone_stalkers.balance.items import HEAL_ITEM_TYPES
        heal_type = next(iter(HEAL_ITEM_TYPES))
        agent["inventory"].append(_make_item(heal_type, "h1", 50))

        result = _run_maintenance("bot1", agent, state)

        assert result is None, "fully equipped agent should return None (run pipeline)"

    def test_pickup_ammo_from_ground_when_weapon_equipped(self):
        """Weapon equipped but no ammo in inventory, ammo on ground → picks it up."""
        agent = make_agent(has_weapon=True, has_armor=True, has_ammo=False)
        # Remove any ammo that make_agent may have added
        agent["inventory"] = [i for i in agent["inventory"] if "ammo" not in i["type"]]
        state = make_minimal_state(agent=agent)
        ammo_on_ground = _make_item("ammo_9mm", "amm2", 30)
        state["locations"]["loc_a"]["items"] = [ammo_on_ground]

        result = _run_maintenance("bot1", agent, state)

        assert result is not None, "should pick up ammo from ground"
        assert any(i["type"] == "ammo_9mm" for i in agent["inventory"])


class TestBrainV3ContextWritten:
    """The NPC Brain decision pipeline populates agent['brain_v3_context'] after the pipeline."""

    def test_brain_v3_context_keys_present(self):
        """After the inner pipeline, agent.brain_v3_context has all required keys."""
        from app.games.zone_stalkers.rules.tick_rules import _run_bot_decision_v2_inner
        agent = make_agent(has_weapon=True, has_armor=True, has_ammo=True)
        # Add a heal item so equipment maintenance step 8 doesn't short-circuit
        from app.games.zone_stalkers.balance.items import HEAL_ITEM_TYPES
        heal_type = next(iter(HEAL_ITEM_TYPES))
        agent["inventory"].append({"id": "h1", "type": heal_type, "name": heal_type, "value": 50})
        state = make_minimal_state(agent=agent)

        _run_bot_decision_v2_inner("bot1", agent, state, 100)

        ctx = agent.get("brain_v3_context")
        assert ctx is not None, "brain_v3_context must be written by the pipeline"
        assert "need_scores" in ctx
        assert "intent_kind" in ctx
        assert "intent_score" in ctx
        assert "intent_reason" in ctx

    def test_brain_v3_context_need_scores_is_dict(self):
        """need_scores in brain_v3_context is a dict of float values."""
        from app.games.zone_stalkers.rules.tick_rules import _run_bot_decision_v2_inner
        agent = make_agent(has_weapon=True, has_armor=True, has_ammo=True)
        from app.games.zone_stalkers.balance.items import HEAL_ITEM_TYPES
        heal_type = next(iter(HEAL_ITEM_TYPES))
        agent["inventory"].append({"id": "h1", "type": heal_type, "name": heal_type, "value": 50})
        state = make_minimal_state(agent=agent)

        _run_bot_decision_v2_inner("bot1", agent, state, 100)

        scores = agent["brain_v3_context"]["need_scores"]
        assert isinstance(scores, dict)
        assert all(isinstance(v, float) for v in scores.values())

    def test_brain_v3_context_intent_score_in_range(self):
        """intent_score in brain_v3_context is a float in [0.0, 1.0]."""
        from app.games.zone_stalkers.rules.tick_rules import _run_bot_decision_v2_inner
        agent = make_agent(has_weapon=True, has_armor=True, has_ammo=True)
        from app.games.zone_stalkers.balance.items import HEAL_ITEM_TYPES
        heal_type = next(iter(HEAL_ITEM_TYPES))
        agent["inventory"].append({"id": "h1", "type": heal_type, "name": heal_type, "value": 50})
        state = make_minimal_state(agent=agent)

        _run_bot_decision_v2_inner("bot1", agent, state, 100)

        score = agent["brain_v3_context"]["intent_score"]
        assert 0.0 <= score <= 1.0
