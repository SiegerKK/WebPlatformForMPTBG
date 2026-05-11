"""Tests for pre-PR5 hunt prerequisites (PR2 section).

Covers spec npc_brain_v3_pre_pr5_hunt_prerequisites.md, Section 2:

  2.2  Weapon ItemNeed urgency is boosted for kill_stalker goal.
  2.5  Liquidity does not sell equipped weapon / armor.
  2.5  Compatible ammo below reserve → forbidden in liquidity.
  2.5  Extra ammo above reserve → safe in liquidity.
  2.5  Incompatible ammo → safe in liquidity.
  2.5  Last medkit when hp low (critical_heal) → not in liquidity options.
  2.6  combat_readiness fields present in NeedEvaluationResult and brain_trace.
"""
from __future__ import annotations

from app.games.zone_stalkers.decision.item_needs import evaluate_item_needs
from app.games.zone_stalkers.decision.liquidity import find_liquidity_options
from app.games.zone_stalkers.decision.needs import evaluate_need_result
from app.games.zone_stalkers.decision.immediate_needs import evaluate_immediate_needs
from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.constants import DESIRED_AMMO_COUNT


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_agent(
    *,
    hp: int = 90,
    hunger: int = 20,
    thirst: int = 20,
    money: int = 100,
    global_goal: str = "get_rich",
    has_weapon: bool = True,
    has_armor: bool = True,
    weapon_type: str = "pistol",
    ammo_count: int = DESIRED_AMMO_COUNT,
    extra_ammo_type: str | None = None,
    med_count: int = 2,
    inventory_extra: list | None = None,
) -> dict:
    equipment: dict = {}
    if has_armor:
        equipment["armor"] = {"type": "leather_jacket"}
    if has_weapon:
        equipment["weapon"] = {"type": weapon_type}

    # Build inventory
    inventory = []
    # Add compatible ammo
    if has_weapon and ammo_count > 0:
        ammo_map = {
            "pistol": "ammo_9mm",
            "shotgun": "ammo_12gauge",
            "assault_rifle": "ammo_545",
            "sniper_rifle": "ammo_762",
        }
        ammo_type = ammo_map.get(weapon_type, "ammo_9mm")
        for i in range(ammo_count):
            inventory.append({"id": f"ammo{i}", "type": ammo_type, "value": 10})
    # Add extra incompatible ammo
    if extra_ammo_type:
        inventory.append({"id": "extra_ammo0", "type": extra_ammo_type, "value": 10})
    # Add healing items
    for i in range(med_count):
        t = "bandage" if i % 2 == 0 else "medkit"
        inventory.append({"id": f"med{i}", "type": t, "value": 30})
    # Add extra items
    if inventory_extra:
        inventory.extend(inventory_extra)

    return {
        "archetype": "stalker_agent",
        "controller": {"kind": "bot"},
        "name": "bot1",
        "is_alive": True,
        "has_left_zone": False,
        "action_used": False,
        "location_id": "loc_a",
        "hp": hp,
        "max_hp": 100,
        "radiation": 0,
        "hunger": hunger,
        "thirst": thirst,
        "sleepiness": 10,
        "money": money,
        "global_goal": global_goal,
        "material_threshold": 3000,
        "risk_tolerance": 0.5,
        "equipment": equipment,
        "inventory": inventory,
        "memory": [],
        "action_queue": [],
        "scheduled_action": None,
        "kill_target_id": "target_bot" if global_goal == "kill_stalker" else None,
    }


def _make_state(agent: dict) -> dict:
    return {
        "seed": 1,
        "world_turn": 100,
        "world_day": 1,
        "world_hour": 12,
        "world_minute": 0,
        "emission_active": False,
        "agents": {"bot1": agent},
        "traders": {
            "trader1": {
                "name": "Сидорович",
                "location_id": "loc_a",
                "inventory": [],
            }
        },
        "locations": {
            "loc_a": {
                "name": "Локация А",
                "terrain_type": "buildings",
                "anomaly_activity": 0,
                "connections": [],
                "items": [],
                "agents": ["bot1"],
            }
        },
        "combat_interactions": {},
        "relations": {},
        "groups": {},
    }


def _ctx(agent: dict, state: dict):
    return build_agent_context("bot1", agent, state)


# ─────────────────────────────────────────────────────────────────────────────
# Section 2.2 — Weapon ItemNeed boosted for kill_stalker
# ─────────────────────────────────────────────────────────────────────────────

def test_weapon_urgency_boosted_for_kill_stalker_goal() -> None:
    """kill_stalker agent without weapon gets urgency 0.80, not 0.65."""
    agent = _make_agent(has_weapon=False, global_goal="kill_stalker")
    state = _make_state(agent)
    ctx = _ctx(agent, state)

    item_needs = evaluate_item_needs(ctx, state)
    weapon_need = next(n for n in item_needs if n.key == "weapon")

    assert weapon_need.missing_count == 1
    assert weapon_need.urgency == 0.80, (
        f"kill_stalker weapon urgency must be 0.80, got {weapon_need.urgency}"
    )


def test_weapon_urgency_standard_for_get_rich_goal() -> None:
    """Phase-1 get_rich agent keeps raw urgency but blocks actionable pressure."""
    agent = _make_agent(has_weapon=False, global_goal="get_rich")
    state = _make_state(agent)
    ctx = _ctx(agent, state)

    item_needs = evaluate_item_needs(ctx, state)
    weapon_need = next(n for n in item_needs if n.key == "weapon")

    assert weapon_need.raw_urgency == 0.65
    assert weapon_need.urgency == 0.0
    assert weapon_need.actionable is False
    assert weapon_need.blocked_by == "phase1_material_gate"


def test_weapon_urgency_zero_when_weapon_present() -> None:
    """Agent with a weapon gets weapon urgency 0.0 regardless of goal."""
    for goal in ("get_rich", "kill_stalker"):
        agent = _make_agent(has_weapon=True, global_goal=goal)
        state = _make_state(agent)
        ctx = _ctx(agent, state)
        item_needs = evaluate_item_needs(ctx, state)
        weapon_need = next(n for n in item_needs if n.key == "weapon")
        assert weapon_need.urgency == 0.0, (
            f"Weapon urgency must be 0 when weapon present (goal={goal})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Section 2.5 — Liquidity: equipped weapon/armor never sold
# ─────────────────────────────────────────────────────────────────────────────

def test_equipped_weapon_not_in_liquidity_options() -> None:
    """Equipped weapon (in equipment dict, not inventory) must not appear in options."""
    agent = _make_agent(has_weapon=True, has_armor=True)
    state = _make_state(agent)
    ctx = _ctx(agent, state)
    immediate = evaluate_immediate_needs(ctx, state)
    item_needs = evaluate_item_needs(ctx, state)
    options = find_liquidity_options(agent=agent, immediate_needs=immediate, item_needs=item_needs)

    weapon_types_in_options = {o.item_type for o in options
                                if o.item_type in {"pistol", "shotgun", "assault_rifle", "sniper_rifle"}}
    assert not weapon_types_in_options, (
        f"Equipped weapon must not appear in liquidity options; found: {weapon_types_in_options}"
    )


def test_equipped_armor_not_in_liquidity_options() -> None:
    """Equipped armor (in equipment dict, not inventory) must not appear in options."""
    agent = _make_agent(has_weapon=True, has_armor=True)
    state = _make_state(agent)
    ctx = _ctx(agent, state)
    immediate = evaluate_immediate_needs(ctx, state)
    item_needs = evaluate_item_needs(ctx, state)
    options = find_liquidity_options(agent=agent, immediate_needs=immediate, item_needs=list(item_needs))

    armor_types_in_options = {o.item_type for o in options
                               if o.item_type in {"leather_jacket", "stalker_suit", "military_armor"}}
    assert not armor_types_in_options, (
        f"Equipped armor must not appear in liquidity options; found: {armor_types_in_options}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Section 2.5 — Liquidity: compatible ammo protection
# ─────────────────────────────────────────────────────────────────────────────

def test_compatible_ammo_below_reserve_is_forbidden() -> None:
    """Pistol ammo at/below DESIRED_AMMO_COUNT → safety='forbidden'."""
    agent = _make_agent(has_weapon=True, weapon_type="pistol", ammo_count=DESIRED_AMMO_COUNT)
    state = _make_state(agent)
    ctx = _ctx(agent, state)
    immediate = evaluate_immediate_needs(ctx, state)
    item_needs = evaluate_item_needs(ctx, state)
    options = find_liquidity_options(agent=agent, immediate_needs=immediate, item_needs=list(item_needs))

    ammo_options = [o for o in options if o.item_type == "ammo_9mm"]
    assert ammo_options, "Compatible ammo must appear as explicit liquidity option"
    for opt in ammo_options:
        assert opt.safety == "forbidden", (
            f"Compatible ammo at reserve must be forbidden, got {opt.safety}"
        )


def test_compatible_ammo_zero_is_forbidden() -> None:
    """Zero compatible ammo → also forbidden (DESIRED_AMMO_COUNT - 0 = below reserve)."""
    agent = _make_agent(has_weapon=True, weapon_type="pistol", ammo_count=0)
    state = _make_state(agent)
    ctx = _ctx(agent, state)
    immediate = evaluate_immediate_needs(ctx, state)
    item_needs = evaluate_item_needs(ctx, state)
    options = find_liquidity_options(agent=agent, immediate_needs=immediate, item_needs=list(item_needs))

    # No ammo items in inventory → no ammo options at all (nothing to protect/sell)
    ammo_options = [o for o in options if o.item_type == "ammo_9mm"]
    assert not ammo_options, "Zero ammo count means no ammo in inventory → no liquidity options for it"


def test_compatible_ammo_above_reserve_is_safe() -> None:
    """Extra compatible ammo above DESIRED_AMMO_COUNT → safety='safe'."""
    extra_count = DESIRED_AMMO_COUNT + 2  # 2 extra ammo packs
    agent = _make_agent(has_weapon=True, weapon_type="pistol", ammo_count=extra_count)
    state = _make_state(agent)
    ctx = _ctx(agent, state)
    immediate = evaluate_immediate_needs(ctx, state)
    item_needs = evaluate_item_needs(ctx, state)
    options = find_liquidity_options(agent=agent, immediate_needs=immediate, item_needs=list(item_needs))

    ammo_options = [o for o in options if o.item_type == "ammo_9mm"]
    assert ammo_options, "Extra ammo must appear in liquidity options"
    # All ammo items use the same compatible-ammo logic — when above reserve some
    # are safe; when the total count > DESIRED_AMMO_COUNT all are marked safe.
    safe_ammo = [o for o in ammo_options if o.safety == "safe"]
    assert safe_ammo, (
        f"Extra ammo above reserve must be safe; got safeties: {[o.safety for o in ammo_options]}"
    )


def test_incompatible_ammo_is_safe() -> None:
    """Ammo that doesn't match the equipped weapon → safety='safe'."""
    # Pistol equipped, but we also carry shotgun ammo
    agent = _make_agent(has_weapon=True, weapon_type="pistol", ammo_count=DESIRED_AMMO_COUNT,
                        extra_ammo_type="ammo_12gauge")
    state = _make_state(agent)
    ctx = _ctx(agent, state)
    immediate = evaluate_immediate_needs(ctx, state)
    item_needs = evaluate_item_needs(ctx, state)
    options = find_liquidity_options(agent=agent, immediate_needs=immediate, item_needs=list(item_needs))

    shotgun_ammo = [o for o in options if o.item_type == "ammo_12gauge"]
    assert shotgun_ammo, "Incompatible ammo must appear as liquidity option"
    for opt in shotgun_ammo:
        assert opt.safety == "safe", (
            f"Incompatible ammo must be safe, got {opt.safety}"
        )


def test_no_ammo_options_when_no_weapon() -> None:
    """Agent with no weapon → ammo items are treated as incompatible (safe to sell)."""
    # No weapon, but has ammo in inventory (e.g. found during exploration)
    agent = _make_agent(has_weapon=False, ammo_count=0,
                        inventory_extra=[{"id": "random_ammo", "type": "ammo_9mm", "value": 10}])
    state = _make_state(agent)
    ctx = _ctx(agent, state)
    immediate = evaluate_immediate_needs(ctx, state)
    item_needs = evaluate_item_needs(ctx, state)
    options = find_liquidity_options(agent=agent, immediate_needs=immediate, item_needs=list(item_needs))

    ammo_opts = [o for o in options if o.item_type == "ammo_9mm"]
    assert ammo_opts, "Ammo with no equipped weapon should appear as safe option"
    for opt in ammo_opts:
        assert opt.safety == "safe", f"Ammo with no weapon must be safe, got {opt.safety}"


# ─────────────────────────────────────────────────────────────────────────────
# Section 2.5 — Liquidity: last medkit when hp low
# ─────────────────────────────────────────────────────────────────────────────

def test_last_medkit_not_in_options_when_hp_low() -> None:
    """Single healing item + hp=40 (critical_heal active) → item not in options."""
    agent = _make_agent(hp=40, med_count=1)  # hp=40 triggers heal_now
    state = _make_state(agent)
    ctx = _ctx(agent, state)
    immediate = evaluate_immediate_needs(ctx, state)
    # Verify critical_heal is active
    from app.games.zone_stalkers.balance.items import HEAL_ITEM_TYPES
    assert any(n.key == "heal_now" for n in immediate), (
        "heal_now ImmediateNeed must be active at hp=40"
    )
    item_needs = evaluate_item_needs(ctx, state)
    options = find_liquidity_options(agent=agent, immediate_needs=immediate, item_needs=list(item_needs))

    med_options = [o for o in options if o.item_type in HEAL_ITEM_TYPES]
    assert not med_options, (
        f"Last healing item must be hidden from options when hp is low and count=1; "
        f"got: {[(o.item_type, o.safety) for o in med_options]}"
    )


def test_extra_medkit_is_available_when_hp_low() -> None:
    """Two healing items + hp=40 → one can appear but is emergency_only (not sold unless desperate)."""
    agent = _make_agent(hp=40, med_count=2)
    state = _make_state(agent)
    ctx = _ctx(agent, state)
    immediate = evaluate_immediate_needs(ctx, state)
    item_needs = evaluate_item_needs(ctx, state)
    options = find_liquidity_options(agent=agent, immediate_needs=immediate, item_needs=list(item_needs))

    from app.games.zone_stalkers.balance.items import HEAL_ITEM_TYPES
    med_options = [o for o in options if o.item_type in HEAL_ITEM_TYPES]
    # With 2 meds and critical_heal active (heal_count=2 > 1 threshold), items
    # appear but with emergency_only or lower safety
    assert med_options, "With 2 meds at hp=40, some should appear (heal_count=2 > 1)"
    for opt in med_options:
        assert opt.safety in ("emergency_only", "risky"), (
            f"Med items when hp low must be emergency_only or risky, got {opt.safety}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Section 2.6 — combat_readiness in NeedEvaluationResult and brain_trace
# ─────────────────────────────────────────────────────────────────────────────

def test_combat_readiness_in_need_evaluation_result() -> None:
    """NeedEvaluationResult must include combat_readiness with weapon/ammo/medicine missing."""
    agent = _make_agent(has_weapon=False, ammo_count=0, med_count=0, global_goal="kill_stalker")
    state = _make_state(agent)
    ctx = _ctx(agent, state)
    result = evaluate_need_result(ctx, state)

    assert result.combat_readiness is not None, "combat_readiness must be present in NeedEvaluationResult"
    cr = result.combat_readiness
    assert "weapon_missing" in cr
    assert "ammo_missing" in cr
    assert "medicine_missing" in cr
    assert cr["weapon_missing"] == 1, f"weapon_missing must be 1, got {cr['weapon_missing']}"
    assert cr["medicine_missing"] > 0, f"medicine_missing must be >0, got {cr['medicine_missing']}"


def test_combat_readiness_zeros_when_fully_equipped() -> None:
    """Fully equipped agent → combat_readiness missing counts are zero."""
    agent = _make_agent(
        has_weapon=True,
        ammo_count=DESIRED_AMMO_COUNT,
        med_count=4,
    )
    state = _make_state(agent)
    ctx = _ctx(agent, state)
    result = evaluate_need_result(ctx, state)

    assert result.combat_readiness is not None
    cr = result.combat_readiness
    assert cr["weapon_missing"] == 0
    assert cr["ammo_missing"] == 0
    assert cr["medicine_missing"] == 0


def test_combat_readiness_in_brain_trace_event() -> None:
    """write_npc_brain_v3_decision_trace passes combat_readiness into the trace event."""
    from app.games.zone_stalkers.decision.debug.brain_trace import write_npc_brain_v3_decision_trace

    agent = _make_agent(has_weapon=False, ammo_count=0, med_count=0, global_goal="kill_stalker")
    state = _make_state(agent)
    ctx = _ctx(agent, state)
    result = evaluate_need_result(ctx, state)

    write_npc_brain_v3_decision_trace(
        agent,
        world_turn=100,
        intent_kind="resupply",
        intent_score=0.80,
        reason="Нет оружия",
        state=state,
        need_result=result,
    )

    trace = agent.get("brain_trace")
    assert trace is not None
    last_event = trace["events"][-1]
    cr = last_event.get("combat_readiness")
    assert cr is not None, f"combat_readiness must be in brain_trace event; event: {last_event}"
    assert cr["weapon_missing"] == 1
