# PR Spec: Hunter Preparation, Sleeping Perception Suppression, and Trader-Stall Fixes

## Context

After PR #44, several old issues improved:

- `GATHER_INTEL → wait` loop no longer repeats in the observed post-update window.
- Fresh trader intel is no longer suppressed by old `target_not_found`.
- `RESUPPLY_* → GET_RICH` fallback is now visible via `plan_fallback`.
- Corpse-loot has state-based validation.
- Semantic combat summaries are no longer shown as raw combat events.

However, the latest logs exposed two new/remaining behavioral issues:

1. **Sleeping killers recorded direct observation of the target.**
   - Killer 0 slept from `turn 4263` to `4473`.
   - Killer 1 slept from `turn 4321` to `4591`.
   - Target `agent_debug_0` visited trader bunker `loc_debug_61` around `turn 4383–4391`.
   - Both sleeping killers got:
     ```text
     known_npcs["agent_debug_0"].last_direct_seen_turn = 4391
     source = direct_observation
     ```
   - This is incorrect. Sleeping/unconscious NPCs should not directly observe co-located agents.

2. **Killers remain parked at trader because survival credit + soft needs + rest dominate behavior.**
   - They buy water/food on credit.
   - Then sleep.
   - Then need food/water again.
   - They do not leave to earn money or improve equipment.
   - Current hunt fallback says “go earn money” mostly when there is no actionable intel, but after PR #44 they often do have a weak/actionable lead, so money-prep pressure does not activate.

The more systematic fix is not just:

```text
no money → earn money
```

It should be:

```text
if hunter learns the target's equipment/strength,
hunter must estimate what gear is needed to beat the target,
then earn/buy until hunter has a clear advantage.
```

This document specifies the complete technical fix set.

---

# 1. Design principles

## 1.1 Hunter behavior should be preparation-driven

For `global_goal == "kill_stalker"`:

```text
see target / hear about target / learn target equipment
→ estimate target combat value
→ compare own equipment against target
→ if not advantaged: prepare/earn/buy
→ if advantaged and target actionable: hunt/engage
```

This is more robust than only reacting to lack of intel or lack of money.

## 1.2 Sleeping NPCs have no direct perception

If an NPC is sleeping/unconscious/downed:

```text
no co-located NPC direct observation
no target_seen
no last_direct_seen_turn update
no visible_now/co_located target belief
```

If target is still present after the hunter wakes up, observation should happen then.

If target leaves before wake-up, hunter should not magically know it was there.

## 1.3 Trader should not become an infinite hunter parking loop

Survival credit is allowed and important, but it should not trap killers at trader forever.

After immediate survival is stabilized, a poorly prepared hunter should leave to earn/prep.

---

# 2. Current code issues

## 2.1 Combat readiness is too coarse

Current `evaluate_kill_target_combat_readiness(...)` checks:

- weapon present;
- ammo available;
- armor present;
- HP > 35;
- target strength threshold;
- money missing for resupply.

This is useful, but insufficient. It does not calculate a **required equipment target** based on the target's known gear/value.

Required improvement:

```text
target known rifle + armor + high strength
→ hunter needs better weapon/ammo/armor/meds
→ hunter should not idle at trader or casually track with weak gear
```

## 2.2 Context builder treats all co-located agents as visible

`build_agent_context(...)` currently populates `visible_entities` from all living co-located agents. It does not check whether the observing agent is sleeping or perception-suppressed.

This caused sleeping killers to record direct observation.

## 2.3 Hunt economy fallback is too narrow

Current logic triggers money-prep mainly when there is no actionable lead:

```text
no possible_locations
no likely_routes
target not visible/co-located
money low
→ GET_MONEY_FOR_RESUPPLY
```

But after PR #44, killers often have a weak/old location lead, so this branch does not activate.

We need:

```text
target not visible now
hunter not sufficiently prepared against known target equipment
→ GET_MONEY_FOR_HUNT / PREPARE_FOR_HUNT
```

even if some possible location exists.

---

# 3. New concepts

## 3.1 Hunter equipment advantage

Add a helper:

```python
def evaluate_hunter_equipment_advantage(
    *,
    agent: dict[str, Any],
    target_belief: Any,
    need_result: Any,
    world_turn: int,
) -> dict[str, Any]:
    ...
```

Return:

```python
{
    "target_equipment_known": True,
    "target_weapon_class": "rifle",
    "target_armor_class": "light",
    "target_combat_strength": 0.94,

    "own_weapon_class": "pistol",
    "own_armor_class": "light",
    "own_ammo_count": 3,
    "own_med_count": 0,
    "own_hp": 57,

    "advantage_score": -0.42,
    "required_advantage_score": 0.20,

    "is_advantaged": False,

    "missing_requirements": [
        "weapon_upgrade",
        "ammo_resupply",
        "medicine_resupply",
    ],

    "estimated_money_needed": 2400,

    "recommended_support_objective": "GET_MONEY_FOR_RESUPPLY",
}
```

## 3.2 Equipment class ranking

Suggested ranking:

```python
WEAPON_CLASS_RANK = {
    "none": 0,
    "melee": 1,
    "pistol": 2,
    "shotgun": 3,
    "rifle": 4,
    "sniper": 5,
}

ARMOR_CLASS_RANK = {
    "none": 0,
    "unknown": 0,
    "light": 1,
    "medium": 2,
    "heavy": 3,
}
```

## 3.3 Advantage scoring

Simple MVP:

```python
own_weapon_score = WEAPON_CLASS_RANK[own_weapon_class]
target_weapon_score = WEAPON_CLASS_RANK[target_weapon_class]

own_armor_score = ARMOR_CLASS_RANK[own_armor_class]
target_armor_score = ARMOR_CLASS_RANK[target_armor_class]

weapon_delta = own_weapon_score - target_weapon_score
armor_delta = own_armor_score - target_armor_score
ammo_bonus = min(0.25, own_ammo_count / 40)
med_bonus = min(0.20, own_med_count / 3)
hp_bonus = (own_hp - 60) / 200

target_strength_penalty = target_combat_strength * 0.5

advantage_score = (
    weapon_delta * 0.35
    + armor_delta * 0.25
    + ammo_bonus
    + med_bonus
    + hp_bonus
    - target_strength_penalty
)
```

MVP does not need perfect combat math. It only needs to distinguish:

```text
pistol/no armor/low meds vs target with rifle/light armor/high strength
→ not ready
```

## 3.4 Required advantage

Suggested rule:

```python
required_advantage_score = 0.2
```

If target is strong:

```python
if target_combat_strength >= 0.8:
    required_advantage_score = 0.35
```

If target is currently co-located and hunter must act opportunistically:

```python
required_advantage_score = 0.1
```

---

# 4. Fix A — sleeping perception suppression

## 4.1 Add helper

Create or add to a shared decision utility module:

```python
def is_perception_suppressed(agent: dict[str, Any]) -> bool:
    ...
```

Return `True` if:

```text
agent is dead
agent has_left_zone
scheduled_action.kind == sleep_for_hours
scheduled_action.action_type == sleep_for_hours
active_plan current step is sleep_for_hours
agent status/state is sleeping/unconscious/downed
```

Be permissive with field names because current state may store sleep in different layers:

```python
scheduled = agent.get("scheduled_action") or {}
kind = str(scheduled.get("kind") or scheduled.get("action_type") or scheduled.get("type") or "")
if kind == "sleep_for_hours":
    return True

runtime = agent.get("active_plan_runtime") or {}
# Or active_plan current step if available.
```

## 4.2 Apply in context builder

In:

```text
backend/app/games/zone_stalkers/decision/context_builder.py
```

Before collecting visible co-located agents:

```python
observer_perception_suppressed = is_perception_suppressed(agent)
```

If suppressed:

```python
visible_entities = []
```

At minimum, do not add co-located NPCs/traders as direct visible entities.

Optional: keep system-level awareness, but do not allow NPC direct observations.

## 4.3 Apply in target belief direct co-location shortcut

In:

```text
backend/app/games/zone_stalkers/decision/target_beliefs.py
```

Current PR #44 added direct co-location visibility for kill target.

Change it to:

```python
direct_co_located_target = (
    not is_perception_suppressed(agent)
    and target is alive
    and target.location_id == current_loc
)
```

## 4.4 Knowledge update protection

If any observation writer calls:

```python
upsert_known_npc_observation(..., source="direct_observation")
```

ensure this is not called while `is_perception_suppressed(agent)` is true.

If direct upsert protection is difficult, add a defensive guard in the caller, not in `knowledge_store.py`. The store should not need gameplay context.

## 4.5 Tests

Add:

```python
def test_sleeping_agent_has_no_visible_colocated_npcs(): ...
def test_sleeping_killer_does_not_directly_observe_colocated_target(): ...
def test_sleeping_killer_does_not_update_last_direct_seen_turn(): ...
def test_awake_killer_still_observes_colocated_target_immediately(): ...
```

Acceptance:

```text
sleeping killer does not get visible_now/co_located
sleeping killer does not update known_npcs[target].last_direct_seen_turn
after waking, if target is still co-located, direct observation works
if target left before wake-up, no direct observation is created
```

---

# 5. Fix B — target-equipment-driven hunter preparation

## 5.1 Extend `evaluate_kill_target_combat_readiness`

Current helper should call the new advantage helper.

Add to return payload:

```python
{
    ...
    "equipment_advantage": advantage_result,
    "equipment_advantaged": advantage_result["is_advantaged"],
    "estimated_money_needed_for_advantage": advantage_result["estimated_money_needed"],
}
```

Add reasons:

```python
if target_equipment_known and not is_advantaged:
    reasons.append("equipment_disadvantage")
if "weapon_upgrade" in missing_requirements:
    reasons.append("weapon_inferior")
if "armor_upgrade" in missing_requirements:
    reasons.append("armor_inferior")
if "medicine_resupply" in missing_requirements:
    reasons.append("no_medicine")
if "ammo_resupply" in missing_requirements:
    reasons.append("low_ammo")
```

Recommended support objective:

```python
if "equipment_disadvantage" in reasons:
    recommended_support_objective = (
        OBJECTIVE_GET_MONEY_FOR_RESUPPLY
        if money < estimated_money_needed
        else OBJECTIVE_PREPARE_FOR_HUNT
    )
```

## 5.2 Use known target equipment

Target equipment can come from:

```text
target_belief.combat_strength
knowledge_v1.known_npcs[target_id].equipment_summary
direct visible target snapshot
```

Current logs show this data exists:

```text
weapon_class = rifle
armor_class = light
combat_strength_estimate = 0.94
last_observed_turn = 4391
```

The readiness helper must consume it.

## 5.3 If equipment is unknown

If target equipment is unknown:

```text
do not assume advantage
but do not force expensive prep either
```

Use conservative behavior:

```python
target_equipment_known = False
if no actionable intel:
    gather intel / earn money
elif hunter has very weak kit:
    prepare minimally
else:
    verify lead
```

---

# 6. Fix C — hunter economy pressure even when there is a weak lead

## 6.1 Current problem

After PR #44, killers may have:

```text
possible_locations = [loc_debug_61]
best_location_confidence ≈ 0.435
target not visible now
money = 0
debt > 0
weak kit
```

Because `possible_locations` is not empty, the old “no actionable hunt lead” money fallback does not trigger.

## 6.2 New condition

In objective generator for `global_goal == "kill_stalker"`:

```python
target_not_currently_available = not target_visible_now and not target_co_located

hunter_money_low = int(agent.get("money") or 0) < HUNT_MIN_CASH_RESERVE
hunter_in_debt = int((agent.get("economic_state") or {}).get("debt_total") or 0) > 0

lead_confidence = float(target_belief.best_location_confidence or 0.0) if target_belief else 0.0
lead_age = world_turn - int(target_belief.last_seen_turn or 0) if target_belief and target_belief.last_seen_turn else 999999

lead_is_weak_or_stale = (
    target_belief is None
    or not target_belief.possible_locations
    or lead_confidence < 0.65
    or lead_age > 240
)

hunt_not_ready = (
    bool(blockers)
    or not combat_eval["combat_ready"]
    or not combat_eval.get("equipment_advantaged", False)
)

needs_hunt_preparation = (
    target_not_currently_available
    and hunt_not_ready
    and (hunter_money_low or hunter_in_debt or lead_is_weak_or_stale)
)
```

If true, generate or boost:

```text
GET_MONEY_FOR_RESUPPLY
```

with metadata:

```python
{
    "support_objective_for": "kill_stalker",
    "hunt_stage": "prepare",
    "hunt_preparation_pressure": True,
    "preparation_basis": "target_equipment_advantage",
    "money": agent.money,
    "debt_total": debt_total,
    "combat_ready": combat_eval["combat_ready"],
    "equipment_advantaged": combat_eval["equipment_advantaged"],
    "estimated_money_needed": estimated_money_needed,
    "not_attacking_reasons": combat_eval["reasons"],
    "target_location_confidence": lead_confidence,
    "target_last_seen_age": lead_age,
}
```

## 6.3 Priority

Priority rules:

```text
critical thirst/hunger/heal/emission > immediate survival
visible + co-located + advantaged hunter > ENGAGE_TARGET
visible + co-located + not advantaged > PREPARE_FOR_HUNT / GET_MONEY_FOR_RESUPPLY
target not visible + not advantaged > GET_MONEY_FOR_RESUPPLY
soft food/water/rest < hunter preparation pressure
```

This means:

```text
killer can buy emergency food/water on credit,
but after stabilizing should leave trader to earn/prepare.
```

---

# 7. Fix D — minimum hunt equipment plan

## 7.1 Add explicit preparation target

For kill-stalker NPCs, define minimum operational kit:

```python
HUNT_MIN_CASH_RESERVE = 300
HUNT_MIN_AMMO_ROUNDS = 20
HUNT_MIN_MED_ITEMS = 2
HUNT_MIN_ARMOR_CLASS_FOR_STRONG_TARGET = "medium"
HUNT_MIN_WEAPON_CLASS_FOR_RIFLE_TARGET = "rifle"
```

These can be rough constants.

## 7.2 Preparation objective

`PREPARE_FOR_HUNT` should not be vague. It should have explicit shopping/earning targets.

Metadata:

```python
{
    "required_items": [
        {"category": "weapon", "min_class": "rifle"},
        {"category": "armor", "min_class": "medium"},
        {"category": "ammo", "min_count": 20},
        {"category": "medical", "min_count": 2},
    ],
    "estimated_money_needed": 2400,
}
```

## 7.3 Planner behavior

If `PREPARE_FOR_HUNT` and enough money:

```text
travel_to_trader
buy missing weapon/ammo/armor/meds
```

If not enough money:

```text
GET_MONEY_FOR_RESUPPLY / get_rich
```

Do not let `PREPARE_FOR_HUNT` become a no-op or wait.

---

# 8. Fix E — trader survival-credit loop anti-pattern

## 8.1 Problem

Unlimited survival credit is correct for survival, but it creates a parking loop:

```text
restore water on credit
restore food on credit
sleep
restore water on credit
restore food on credit
sleep
...
```

For ordinary NPCs this may be acceptable. For killers, it prevents mission progress.

## 8.2 Anti-loop policy for hunters

For `global_goal == "kill_stalker"`:

After immediate critical needs are stabilized:

```text
if money == 0
and debt_total > 0
and no visible target
and not equipment_advantaged
then prefer GET_MONEY_FOR_RESUPPLY over soft RESTORE_* and REST.
```

Critical means:

```text
drink_now/eat_now/heal_now urgency >= 0.8
emission active/scheduled
```

Soft means:

```text
resupply/rest/restore with urgency below critical threshold
```

## 8.3 Tests

```python
def test_killer_after_survival_credit_does_not_repeat_soft_trader_loop(): ...
def test_killer_can_take_emergency_survival_credit_before_earning(): ...
def test_killer_with_soft_hunger_and_no_money_prefers_get_money_for_hunt(): ...
```

---

# 9. Fix F — wake/sleep semantics

## 9.1 Do not wake on unseen target

If sleeping hunter cannot perceive, then target entering the location should not wake them.

Correct behavior:

```text
target enters while hunter sleeps
hunter does not know
hunter continues sleep
target leaves before hunter wakes
hunter has no direct observation
```

## 9.2 Optional future behavior

If we later add “loud combat/noise/witness wakes NPC”, implement as separate event:

```text
noise_event / alarm_event / witness_alerted_hunter
```

Do not conflate this with direct observation.

For this PR:

```text
sleeping = no direct observation
```

---

# 10. Fix G — debug visibility

Add fields to `brain_v3_context` when hunter logic runs:

```python
"hunter_preparation": {
    "active": True,
    "target_equipment_known": True,
    "target_weapon_class": "rifle",
    "target_armor_class": "light",
    "target_combat_strength": 0.94,
    "own_weapon_class": "pistol",
    "own_armor_class": "light",
    "own_ammo_count": 3,
    "own_med_count": 0,
    "advantage_score": -0.42,
    "required_advantage_score": 0.35,
    "is_advantaged": False,
    "missing_requirements": ["weapon_upgrade", "ammo_resupply", "medicine_resupply"],
    "estimated_money_needed": 2400,
    "recommended_support_objective": "GET_MONEY_FOR_RESUPPLY",
}
```

In NPC profile UI show:

```text
🎯 Подготовка к охоте
Цель: rifle / light / strength 0.94
Я: pistol / light / ammo 3 / meds 0
Преимущество: нет
Нужно: rifle, ammo, meds
Оценка денег: 2400 RU
```

This makes future log analysis much easier.

---

# 11. Tests

## 11.1 Sleeping perception tests

```python
def test_sleeping_agent_has_no_visible_colocated_npcs(): ...
def test_sleeping_killer_does_not_directly_observe_colocated_target(): ...
def test_sleeping_killer_does_not_update_last_direct_seen_turn(): ...
def test_awake_killer_still_observes_colocated_target_immediately(): ...
```

## 11.2 Hunter equipment advantage tests

```python
def test_hunter_infers_target_equipment_from_known_npc_summary(): ...
def test_pistol_hunter_vs_rifle_target_is_not_equipment_advantaged(): ...
def test_hunter_with_better_weapon_armor_ammo_and_meds_is_advantaged(): ...
def test_known_target_equipment_generates_money_for_hunt_when_undergeared(): ...
```

## 11.3 Hunter economy pressure tests

```python
def test_killer_with_actionable_but_weak_lead_and_no_money_prefers_get_money_for_hunt(): ...
def test_killer_with_known_strong_target_and_no_money_prefers_get_money_for_hunt(): ...
def test_killer_at_trader_with_debt_and_soft_hunger_leaves_to_earn(): ...
def test_critical_hunger_still_allows_survival_credit_before_earning(): ...
def test_visible_colocated_target_with_advantage_overrides_get_money(): ...
def test_visible_colocated_target_without_advantage_prefers_prepare_not_engage(): ...
```

## 11.4 Trader-loop regression tests

```python
def test_killer_after_survival_credit_does_not_repeat_soft_trader_loop(): ...
def test_killer_soft_rest_does_not_dominate_hunt_preparation_when_not_critical(): ...
```

## 11.5 Debug tests

```python
def test_hunter_preparation_debug_context_contains_target_and_own_equipment(): ...
def test_hunter_preparation_ui_projection_contains_missing_requirements(): ...
```

---

# 12. Acceptance criteria

This PR is complete when:

```text
[ ] Sleeping/unconscious agents do not receive direct observations.
[ ] Sleeping killer does not update last_direct_seen_turn when target visits location.
[ ] Awake co-located killer still observes target immediately.
[ ] Target equipment knowledge is used to compute hunter preparation requirements.
[ ] Hunter with inferior equipment prefers GET_MONEY_FOR_RESUPPLY / PREPARE_FOR_HUNT.
[ ] Hunter does not attack strong visible target when undergeared unless design explicitly allows desperation attacks.
[ ] Hunter with no money/debt and weak gear leaves trader to earn after critical survival is stabilized.
[ ] Soft RESTORE_FOOD/WATER/REST does not keep kill_stalker NPC parked at trader forever.
[ ] Critical survival still overrides hunt/economy.
[ ] Debug profile exposes hunter preparation reasoning.
```

---

# 13. Suggested Copilot task

```text
Extend copilot/fix-npc-behavior with hunter preparation logic.

Implement perception suppression for sleeping/unconscious agents so sleeping killers do not directly observe co-located targets.

Then replace the narrow "no intel -> get money" hunt fallback with equipment-driven preparation:
when a kill_stalker NPC learns or sees target equipment/strength, compare hunter equipment against target equipment. If hunter is not advantaged, generate/boost GET_MONEY_FOR_RESUPPLY or PREPARE_FOR_HUNT even if there is a weak possible location lead.

Add anti-loop behavior so kill_stalker NPCs do not stay at trader forever cycling survival credit + food/water + sleep. Critical survival still wins, but after stabilization they should leave to earn and improve gear.

Add debug context `hunter_preparation` showing target equipment, own equipment, advantage score, missing requirements, estimated money needed, and recommended support objective.

Add tests for sleeping perception, equipment advantage, hunter economy pressure, and trader-loop regression.
```

---

# 14. Expected long-run behavior

After this PR, logs should show:

```text
target visits trader while killers sleep
→ no direct observation is recorded

killers wake up
→ if target already left, they do not magically know it was there

killer learns target has rifle/light armor/high strength
→ compares own pistol/light armor/low ammo
→ chooses GET_MONEY_FOR_HUNT / PREPARE_FOR_HUNT

killer buys survival food/water only when critical
→ then leaves trader to earn money / find artifacts / buy better kit

if target becomes visible while hunter is awake and advantaged
→ ENGAGE_TARGET
```

The intended loop becomes:

```text
learn target strength
→ prepare to exceed target
→ earn/buy missing gear
→ track/verify lead
→ attack only when actually ready or when design allows desperation
```
