# NPC Brain v3 — Future PR5 E2E Test Scenario and Complex `kill_stalker` Logic

> Назначение: документ-спецификация для будущего end-to-end теста после реализации PR 5.  
> Цель: дать Copilot/разработчику достаточно информации, чтобы написать тест, проверяющий сложную цепочку поведения NPC через всю архитектуру:
>
> ```text
> Memory → BeliefState → NeedEvaluationResult → Objectives → ActivePlan → PlanStep → scheduled_action/runtime bridge → brain_trace/memory
> ```
>
> Дополнительно документ описывает, как эта же архитектура должна решать более сложную цель: `kill_stalker`.

---

# Part 1 — Future PR5 E2E Test Scenario

---

## 1. High-level scenario

Тестовый NPC беден, без оружия, но хочет выжить и развиваться.

Он:

1. Понимает, что ему нужно оружие.
2. Понимает, что денег на оружие не хватает.
3. Вспоминает место, где можно найти артефакт.
4. Строит план добыть деньги через артефакт.
5. По пути хочет пить.
6. Временно ставит основной план на паузу.
7. Пьёт воду.
8. Возобновляет основной план.
9. Получает предупреждение о выбросе.
10. Ставит план на паузу.
11. Уходит в укрытие.
12. Пережидает выброс.
13. Возобновляет или ремонтирует старый план.
14. Находит артефакт.
15. Идёт к торговцу.
16. Продаёт артефакт.
17. Покупает оружие и патроны.
18. Завершает objective.

Это проверяет:

```text
PR 1:
  PlanMonitor
  scheduled_action safety
  emission interruption
  brain_trace

PR 2:
  ImmediateNeed
  ItemNeed
  liquidity
  no unaffordable buy loop
  survival consumption

PR 3:
  MemoryStore
  BeliefState
  memory_used

PR 4:
  Objective generation
  Objective scoring
  rejected alternatives
  continue-vs-switch

PR 5:
  ActivePlan source of truth
  pause/resume
  repair
  PlanStep lifecycle
  runtime bridge
```

---

## 2. Suggested test name

```python
def test_pr5_active_plan_survives_survival_interrupt_and_emission_then_repairs_and_completes_resupply_weapon():
    ...
```

Shorter possible name:

```python
def test_pr5_resupply_weapon_plan_pauses_for_water_and_emission_then_resumes():
    ...
```

---

## 3. Initial world setup

### 3.1. Locations

```python
locations = {
    "loc_village": {
        "id": "loc_village",
        "name": "Деревня новичков",
        "terrain_type": "hamlet",
        "neighbors": {"loc_road": {"travel_time": 5}},
    },
    "loc_road": {
        "id": "loc_road",
        "name": "Старая дорога",
        "terrain_type": "plain",
        "neighbors": {
            "loc_village": {"travel_time": 5},
            "loc_anomaly_field": {"travel_time": 8},
            "loc_shelter": {"travel_time": 4},
        },
    },
    "loc_anomaly_field": {
        "id": "loc_anomaly_field",
        "name": "Электрическое поле",
        "terrain_type": "plain",
        "anomaly_activity": 9,
        "artifacts": [
            {
                "id": "artifact_1",
                "type": "sparkler",
                "name": "Вспышка",
                "value": 2000,
            }
        ],
        "neighbors": {
            "loc_road": {"travel_time": 8},
            "loc_shelter": {"travel_time": 5},
        },
    },
    "loc_shelter": {
        "id": "loc_shelter",
        "name": "Старый подвал",
        "terrain_type": "buildings",
        "neighbors": {
            "loc_road": {"travel_time": 4},
            "loc_anomaly_field": {"travel_time": 5},
            "loc_trader": {"travel_time": 10},
        },
    },
    "loc_trader": {
        "id": "loc_trader",
        "name": "Бункер торговца",
        "terrain_type": "scientific_bunker",
        "neighbors": {"loc_shelter": {"travel_time": 10}},
        "trader": {
            "id": "trader_1",
            "name": "Гнидорович",
            "buys_artifacts": True,
            "inventory": [
                {
                    "id": "weapon_ak_1",
                    "type": "ak74",
                    "name": "АК-74",
                    "price": 1500,
                    "value": 1500,
                },
                {
                    "id": "ammo_545_1",
                    "type": "ammo_545",
                    "name": "5.45x39",
                    "price": 100,
                    "value": 100,
                },
                {
                    "id": "water_trader_1",
                    "type": "water",
                    "name": "Вода",
                    "price": 45,
                    "value": 45,
                },
                {
                    "id": "bread_trader_1",
                    "type": "bread",
                    "name": "Буханка хлеба",
                    "price": 30,
                    "value": 30,
                },
            ],
        },
    },
}
```

### 3.2. NPC

```python
agent = {
    "id": "agent_test_1",
    "name": "Тестовый сталкер",
    "archetype": "stalker_agent",
    "controller": {"kind": "bot"},
    "is_alive": True,
    "has_left_zone": False,

    "location_id": "loc_village",

    "hp": 100,
    "max_hp": 100,
    "hunger": 35,
    "thirst": 65,
    "sleepiness": 20,
    "radiation": 0,

    "money": 40,
    "global_goal": "get_rich",
    "current_goal": None,
    "risk_tolerance": 0.6,

    "inventory": [
        {
            "id": "water_1",
            "type": "water",
            "name": "Вода",
            "value": 45,
        }
    ],

    "equipment": {
        "weapon": None,
        "armor": {
            "id": "armor_1",
            "type": "leather_jacket",
            "name": "Кожаная куртка",
            "value": 300,
        },
        "detector": None,
    },

    "scheduled_action": None,
    "action_queue": [],
    "active_plan_v3": None,
}
```

### 3.3. Memory / Belief setup

For PR 3+ tests, NPC should have memory records like:

```python
memory_v3_records = [
    {
        "id": "mem_known_anomaly_1",
        "layer": "spatial",
        "kind": "known_artifact_source",
        "summary": "В Электрическом поле часто появляются артефакты.",
        "location_id": "loc_anomaly_field",
        "tags": ["artifact", "anomaly", "money_source"],
        "confidence": 0.9,
        "importance": 0.8,
    },
    {
        "id": "mem_known_trader_1",
        "layer": "semantic",
        "kind": "trader_location_known",
        "summary": "Гнидорович находится в Бункере торговца.",
        "location_id": "loc_trader",
        "entity_ids": ["trader_1"],
        "tags": ["trader", "trade"],
        "confidence": 0.95,
        "importance": 0.8,
    },
    {
        "id": "mem_trader_buys_artifacts",
        "layer": "semantic",
        "kind": "trader_buys_artifacts",
        "summary": "Гнидорович покупает артефакты.",
        "location_id": "loc_trader",
        "entity_ids": ["trader_1"],
        "tags": ["trader", "artifact_buyer", "sell_artifacts"],
        "confidence": 0.9,
        "importance": 0.7,
    },
    {
        "id": "mem_trader_sells_weapons",
        "layer": "semantic",
        "kind": "trader_sells_weapon",
        "summary": "Гнидорович продаёт оружие.",
        "location_id": "loc_trader",
        "entity_ids": ["trader_1"],
        "item_types": ["ak74"],
        "tags": ["trader", "weapon", "resupply"],
        "confidence": 0.85,
        "importance": 0.7,
    },
]
```

If `memory_v3` is not physically available yet in the test harness, equivalent legacy memories may be inserted and bridged.

### 3.4. Emission setup

The test should schedule an emission so that warning happens while the NPC is near or inside the anomaly field.

```python
state["emission_scheduled_turn"] = state["world_turn"] + 30
state["emission_warning_offset"] = 12
state["emission_warning_written_turn"] = None
state["emission_active"] = False
```

Alternative deterministic setup:

```python
# Directly add an emission_imminent memory at the desired test phase.
agent["memory"].append({
    "world_turn": state["world_turn"],
    "type": "observation",
    "title": "⚠️ Скоро выброс!",
    "effects": {
        "action_kind": "emission_imminent",
        "turns_until": 12,
        "emission_scheduled_turn": state["world_turn"] + 12,
    },
    "summary": "Скоро будет выброс — примерно через 12 ходов",
})
```

---

# 4. Expected behavior timeline

---

## T0 — Initial decision

### Expected reasoning

`ImmediateNeed`:

```text
drink_now:
  thirst = 65
  below critical
  not blocking

eat_now:
  hunger = 35
  not active

heal_now:
  hp = 100
  not active
```

`ItemNeed`:

```text
weapon:
  urgency = 0.65
  missing_count = 1
  affordability_hint = unaffordable

ammo:
  inactive or lower priority because no weapon yet

food/drink stock:
  possible but lower than weapon
```

`Liquidity`:

```text
weapon price = 1500
money = 40
money_missing = 1460
safe_sale_options = 0
```

`MemoryStore retrieval`:

```text
known artifact source
known trader
trader buys artifacts
trader sells weapons
```

### Expected selected objective

```text
GET_MONEY_FOR_RESUPPLY
```

or a more specific equivalent:

```text
FIND_ARTIFACT_TO_BUY_WEAPON
```

### Expected ActivePlan

```text
ActivePlan objective:
  GET_MONEY_FOR_RESUPPLY

Steps:
  1. travel_to_location loc_anomaly_field
  2. explore_anomaly_location loc_anomaly_field
  3. pickup_artifact
  4. travel_to_location loc_trader
  5. trade_sell_item artifact
  6. trade_buy_item weapon
  7. trade_buy_item ammo
```

### Assertions

```python
assert agent["active_plan_v3"]["objective_key"] in (
    "GET_MONEY_FOR_RESUPPLY",
    "FIND_ARTIFACT_TO_BUY_WEAPON",
)

assert first_step_kind(agent) == "travel_to_location"
assert current_runtime_action(agent)["type"] == "travel"
assert brain_trace_active_objective(agent) == agent["active_plan_v3"]["objective_key"]
assert brain_trace_contains_memory_used(agent, "known_artifact_source")
assert brain_trace_contains_memory_used(agent, "trader_location_known")
```

---

## T1 — Travel begins

Runtime bridge should create:

```text
scheduled_action = travel_to_location loc_anomaly_field
```

or equivalent multi-hop travel.

### Assertions

```python
assert agent["scheduled_action"] is not None
assert agent["active_plan_v3"]["status"] == "active"
assert current_plan_step(agent)["kind"] == "travel_to_location"
```

---

## T2 — Thirst becomes critical during travel

Force or simulate:

```python
agent["thirst"] = 82
agent["inventory"] contains water
```

### Expected behavior

The current money/resupply plan should not be aborted permanently.

Instead:

```text
PlanContinuityDecision = pause
reason = critical_thirst
```

A short objective/plan should run:

```text
RESTORE_WATER
  1. consume water
```

After execution:

```text
water removed from inventory
thirst reduced
GET_MONEY_FOR_RESUPPLY resumes
travel continues or is recreated
```

### Assertions

```python
assert plan_decision(agent) in ("pause", "repair")
assert brain_trace_contains(agent, reason="critical_thirst")
assert consumed_item_type(agent, "water")
assert old_plan_resumed_or_still_recoverable(agent, "GET_MONEY_FOR_RESUPPLY")
assert agent["thirst"] < 82
```

Important invariant:

```python
assert not permanently_aborted(agent, "GET_MONEY_FOR_RESUPPLY")
```

---

## T3 — Emission warning while exploring anomaly field

Set state:

```python
agent["location_id"] = "loc_anomaly_field"
current_plan_step = "explore_anomaly_location"
add emission_imminent memory
```

### Expected behavior

The artifact-money plan is paused, not forgotten.

Selected blocking objective:

```text
REACH_SAFE_SHELTER
```

New temporary plan:

```text
REACH_SAFE_SHELTER
  1. travel_to_location loc_shelter
  2. wait_in_shelter until emission_ended
```

Runtime:

```text
scheduled_action = emergency_flee travel to loc_shelter
```

### Assertions

```python
assert agent["active_plan_v3"]["status"] in ("active", "paused")
assert has_paused_plan(agent, "GET_MONEY_FOR_RESUPPLY")
assert current_objective(agent) == "REACH_SAFE_SHELTER"
assert current_runtime_action(agent).get("emergency_flee") is True
assert brain_trace_contains(agent, reason="emission_threat")
```

Emergency flee invariant:

```python
# PlanMonitor must not interrupt emergency_flee.
assert emergency_flee_not_interrupted_on_next_tick(state)
```

---

## T4 — Emission ends

After `emission_ended`:

```text
NPC alive
location = loc_shelter
old money/resupply plan still valid
```

### Expected behavior

Plan continuity should decide:

```text
resume old plan
```

But route should be repaired because the NPC is now at shelter, not anomaly field.

Repair:

```text
insert/replace travel_to_location loc_anomaly_field
```

### Assertions

```python
assert agent["is_alive"] is True
assert plan_decision(agent) in ("resume", "repair")
assert active_or_resumed_objective(agent) == "GET_MONEY_FOR_RESUPPLY"
assert current_or_next_step_targets(agent, "loc_anomaly_field")
```

---

## T5 — Optional branch: anomaly is empty

This branch is optional but very valuable.

Set:

```text
loc_anomaly_field has no artifacts
explore result = confirmed_empty
```

### Expected behavior

Do not abandon the objective immediately if another remembered money source exists.

Possible behavior:

```text
PlanRepair:
  target next known anomaly location
```

or:

```text
Objective replaced:
  GET_MONEY_FOR_RESUPPLY via another path
```

### Assertions

```python
assert memory_contains(agent, kind="confirmed_empty", location_id="loc_anomaly_field")
assert plan_decision(agent) in ("repair", "replace")
assert not loops_exploring_confirmed_empty_location(agent, "loc_anomaly_field")
```

If this is too much for the first E2E test, keep it for a second test.

---

## T6 — Artifact found and picked up

Expected:

```text
explore_anomaly_location completed
pickup_artifact completed
inventory contains artifact
next step = travel_to_location loc_trader
```

### Assertions

```python
assert inventory_contains_category(agent, "artifact")
assert completed_step(agent, "pickup_artifact")
assert next_or_current_step_kind(agent) == "travel_to_location"
assert next_or_current_step_targets(agent, "loc_trader")
```

---

## T7 — Travel to trader

If hunger/thirst are non-critical:

```text
continue current plan
```

If hunger/thirst become critical and item exists:

```text
pause
consume
resume
```

For the core test, keep hunger/thirst non-critical to avoid too many branches.

### Assertions

```python
assert active_or_resumed_objective(agent) == "GET_MONEY_FOR_RESUPPLY"
assert current_or_next_step_targets(agent, "loc_trader")
```

---

## T8 — Sell artifact

At trader location:

```text
trade_sell_item artifact
money increases
artifact removed
```

### Assertions

```python
assert not inventory_contains_category(agent, "artifact")
assert agent["money"] >= 1500
assert completed_step(agent, "trade_sell_item")
assert memory_contains(agent, kind_or_action="trade_sell")
```

---

## T9 — Buy weapon and ammo

Expected:

```text
trade_buy_item weapon
equipment.weapon is not None

trade_buy_item ammo
inventory contains compatible ammo
```

### Assertions

```python
assert agent["equipment"]["weapon"] is not None
assert has_compatible_ammo(agent)
assert completed_step(agent, "trade_buy_item", item_category="weapon")
assert completed_step(agent, "trade_buy_item", item_category="ammo")
```

---

## T10 — Objective completed

Expected final state:

```text
GET_MONEY_FOR_RESUPPLY completed
RESUPPLY_WEAPON satisfied
agent alive
weapon equipped
ammo present
```

### Assertions

```python
assert agent["is_alive"] is True
assert agent["active_plan_v3"]["status"] == "completed"
assert agent["equipment"]["weapon"] is not None
assert has_compatible_ammo(agent)
assert brain_trace_contains(agent, "completed")
```

---

# 5. Global assertions for the E2E test

Across the whole scenario:

```python
assert agent["is_alive"] is True
assert agent["scheduled_action"] is None or action_matches_current_plan_step(agent)
assert no_unaffordable_buy_loop(agent)
assert no_dead_agent_with_scheduled_action(state)
assert no_repeated_same_plan_monitor_abort_spam(agent)
assert brain_trace_has_active_objective(agent)
assert brain_trace_has_active_plan(agent)
assert memory_used_contains_relevant_records(agent)
```

Specific invariants:

```text
1. The original money/resupply plan is not lost after thirst interruption.
2. The original money/resupply plan is not lost after emission interruption.
3. Emergency flee is not interrupted.
4. After emission, plan is resumed or repaired.
5. Artifact is converted into money.
6. Money is converted into weapon/ammo.
```

---

# 6. Minimum version of this test

If the full scenario is too large for a first PR5 test, start with:

```text
NPC wants to buy weapon but cannot afford it.
He creates a GET_MONEY_FOR_RESUPPLY ActivePlan.
During travel, thirst becomes critical.
He pauses the plan, drinks water, resumes the plan, reaches trader/anomaly target.
```

Test name:

```python
def test_pr5_active_plan_pauses_for_critical_thirst_then_resumes():
    ...
```

This minimal test covers:

```text
ActivePlan
pause
resume
ImmediateNeed interruption
scheduled_action bridge
continue old objective
brain_trace plan_decision
```

Then add the emission branch as a second test.

---

# Part 2 — More complex behavior: kill_stalker objective

---

## 7. Can the same architecture support a more complex goal like killing another stalker?

Yes.

The full PR5 architecture is especially useful for this kind of task, because `kill_stalker` is not a single action.

It is a multi-stage behavior:

```text
identify target
locate target
assess readiness
prepare equipment
travel/intercept
handle interruptions
engage or ambush
confirm kill
escape/recover
update memory/reputation
```

This is exactly the kind of task that should be modeled as:

```text
Objective → ActivePlan → PlanSteps → repair/pause/resume
```

Not as a single `Intent`.

---

## 8. Example initial state for kill target

NPC:

```text
global_goal = kill_stalker
kill_target_id = agent_target_1
weapon = pistol
ammo = low
armor = leather_jacket
hp = 100
money = 300
risk_tolerance = 0.7
```

Target:

```text
agent_target_1:
  is_alive = true
  location_id unknown or last known loc_bar
  faction = enemy
  weapon = ak74
  armor = medium
```

NPC memory:

```text
- target was seen at loc_bar 30 turns ago
- target often travels between loc_bar and loc_checkpoint
- trader at loc_trader sells ammo
- loc_checkpoint has good ambush terrain
- loc_swamp is dangerous
```

---

## 9. Kill target decision chain

### Step 1 — BeliefState

Build beliefs:

```text
known target:
  last_known_location = loc_bar
  confidence = 0.7
  last_seen_turn = 30 turns ago

possible target routes:
  loc_bar → loc_checkpoint

known risks:
  target has better weapon
  target may have allies
  NPC ammo low
```

### Step 2 — NeedEvaluationResult

Before hunting, survival/equipment is checked:

```text
ImmediateNeed:
  none

ItemNeed:
  ammo urgency high if ammo low
  weapon maybe acceptable
  armor acceptable
  medicine stock maybe low
```

If immediate survival is critical:

```text
do not hunt yet
```

### Step 3 — Objective generation

Generate:

```text
HUNT_TARGET
RESUPPLY_AMMO
RESUPPLY_MEDICINE
GET_MONEY_FOR_RESUPPLY
RESTORE_WATER
REST
CONTINUE_CURRENT_PLAN
```

### Step 4 — Objective scoring

If target is known but ammo is too low:

```text
HUNT_TARGET:
  high value
  high risk
  blocked/penalized by low ammo

RESUPPLY_AMMO:
  medium/high urgency
  enables HUNT_TARGET

Selected:
  RESUPPLY_AMMO
```

If ammo is sufficient:

```text
Selected:
  HUNT_TARGET
```

### Step 5 — ActivePlan

If ready to hunt:

```text
Objective: HUNT_TARGET

Steps:
  1. travel_to_last_known_location loc_bar
  2. observe_location_for_target
  3. if target found:
       assess_combat
     else:
       retrieve predicted route / ask trader / search nearby
  4. move_to_intercept_or_ambush_location
  5. engage_target
  6. confirm_target_dead
  7. loot_or_escape
```

If not ready:

```text
Objective: PREPARE_FOR_HUNT

Steps:
  1. travel_to_trader
  2. buy_ammo
  3. buy_medkit if needed
  4. resume HUNT_TARGET
```

---

# 10. How should the system solve `kill_stalker` correctly?

The correct solution is not:

```text
if target exists:
  run toward target and attack
```

That is too simplistic.

Correct solution:

```text
1. Is the target still alive?
2. Do I know where the target is?
3. How confident is that knowledge?
4. Am I equipped to fight?
5. Is the target stronger than me?
6. Are there allies/enemies nearby?
7. Is there a safer interception point?
8. Do I need to prepare first?
9. Can I execute the attack?
10. If the situation changes, should I pause, repair, or abort?
```

---

## 11. Kill target as Objectives

Possible objectives:

```text
LOCATE_TARGET
TRACK_TARGET
PREPARE_FOR_HUNT
INTERCEPT_TARGET
AMBUSH_TARGET
ENGAGE_TARGET
CONFIRM_KILL
ESCAPE_AFTER_KILL
RESUPPLY_AMMO
HEAL_SELF
RETREAT_FROM_TARGET
```

### Example scoring

```text
HUNT_TARGET:
  urgency = goal urgency
  expected_value = high
  confidence = target_location_confidence
  risk = target_strength / self_strength
  time_cost = distance_to_target
  resource_cost = expected ammo/medical use
  goal_alignment = 1.0 because global_goal = kill_stalker
```

If target location confidence is low:

```text
LOCATE_TARGET may score above ENGAGE_TARGET.
```

If ammo is low:

```text
RESUPPLY_AMMO may score above HUNT_TARGET.
```

If hp is low:

```text
HEAL_SELF blocks HUNT_TARGET.
```

---

## 12. Kill target plan states

### Case A — target location known and NPC is ready

```text
HUNT_TARGET
  1. travel_to_location target_location
  2. observe_location_for_target
  3. engage_target
  4. confirm_target_dead
  5. leave_area
```

### Case B — target location unknown

```text
LOCATE_TARGET
  1. retrieve last known locations from memory
  2. travel_to most likely location
  3. observe
  4. ask trader / gather information
  5. update target belief
```

### Case C — NPC not ready

```text
PREPARE_FOR_HUNT
  1. resupply ammo
  2. buy medicine
  3. repair/upgrade weapon if needed
  4. resume HUNT_TARGET
```

### Case D — target too strong

```text
AMBUSH_TARGET
  1. choose ambush location
  2. wait for target or lure target
  3. attack from advantage
```

This is more advanced and can be a later extension.

---

## 13. Plan repair examples for kill target

### Target not at expected location

```text
current step:
  observe_location_for_target

result:
  target not found

repair:
  query MemoryStore for next likely location
  update plan target
  travel_to next location
```

### Target died before NPC arrived

```text
belief:
  target is_alive = false

decision:
  complete or abort HUNT_TARGET
  update global_goal_achieved
```

### NPC becomes thirsty during hunt

```text
pause HUNT_TARGET
RESTORE_WATER
resume HUNT_TARGET
```

### Emission warning during hunt

```text
pause HUNT_TARGET
REACH_SAFE_SHELTER
wait_in_shelter
resume or repair HUNT_TARGET
```

### NPC takes damage in combat

```text
if hp critical:
  pause/abort ENGAGE_TARGET
  HEAL_SELF or ESCAPE_DANGER
```

---

## 14. BrainTrace example for kill target

```json
{
  "current_thought": "Цель — убить сталкера Поцик 2. Его видели у Бара, но у меня мало патронов.",
  "active_objective": {
    "key": "PREPARE_FOR_HUNT",
    "score": 0.74,
    "source": "global_goal",
    "reason": "Цель сильнее, патронов недостаточно"
  },
  "objective_scores": [
    {
      "key": "PREPARE_FOR_HUNT",
      "score": 0.74,
      "decision": "selected",
      "reason": "Покупка патронов снизит риск охоты"
    },
    {
      "key": "HUNT_TARGET",
      "score": 0.58,
      "decision": "rejected",
      "reason": "Недостаточно патронов"
    },
    {
      "key": "REST",
      "score": 0.22,
      "decision": "rejected",
      "reason": "Сонливость низкая"
    }
  ],
  "memory_used": [
    {
      "kind": "target_last_seen",
      "summary": "Поцик 2 был замечен у Бара 30 ходов назад",
      "confidence": 0.7,
      "used_for": "locate_target"
    },
    {
      "kind": "trader_sells_ammo",
      "summary": "Гнидорович продаёт 9мм патроны",
      "confidence": 0.9,
      "used_for": "prepare_for_hunt"
    }
  ],
  "active_plan": {
    "objective_key": "PREPARE_FOR_HUNT",
    "steps": [
      {
        "kind": "travel_to_location",
        "label": "Дойти до торговца",
        "status": "running"
      },
      {
        "kind": "trade_buy_item",
        "label": "Купить патроны",
        "status": "pending"
      },
      {
        "kind": "travel_to_location",
        "label": "Идти к последнему месту цели",
        "status": "pending"
      }
    ]
  }
}
```

---

# 15. Future test scenario for `kill_stalker`

Suggested test name:

```python
def test_pr5_kill_target_prepares_tracks_pauses_for_emission_and_resumes_hunt():
    ...
```

## Initial state

```text
hunter:
  global_goal = kill_stalker
  kill_target_id = target
  hp = 100
  thirst = 30
  hunger = 30
  weapon = pistol
  ammo = 1
  money = 500
  remembers trader sells ammo
  remembers target last seen at loc_bar

target:
  is_alive = true
  location_id = loc_bar
  weapon = ak74
```

## Expected timeline

```text
T0:
  Objective scoring chooses PREPARE_FOR_HUNT, not HUNT_TARGET,
  because ammo is too low.

T1:
  ActivePlan:
    travel_to_trader
    buy_ammo
    travel_to_loc_bar
    observe_target
    engage_target

T2:
  ammo bought.

T3:
  hunter travels to loc_bar.

T4:
  emission warning happens.
  HUNT_TARGET plan is paused.
  REACH_SAFE_SHELTER plan runs.

T5:
  emission ends.
  HUNT_TARGET resumes.

T6:
  target is no longer at loc_bar.
  MemoryStore retrieves next likely location loc_checkpoint.
  PlanRepair replaces target location.

T7:
  hunter travels to loc_checkpoint.
  target found.

T8:
  combat starts.
  hunter wins or test stubs combat result.

T9:
  target dead.
  HUNT_TARGET completed.
```

## Assertions

```python
assert selected_objective_at_t0 == "PREPARE_FOR_HUNT"
assert "HUNT_TARGET" in rejected_or_deferred_objectives

assert active_plan_contains_step("trade_buy_item", item_category="ammo")
assert active_plan_contains_step("observe_location_for_target")
assert active_plan_contains_step("engage_target")

assert plan_paused_for("emission_threat")
assert plan_resumed_after("emission_ended")

assert plan_repaired_when_target_not_found()
assert memory_used_contains("target_last_seen")
assert memory_used_contains("target_predicted_route")

assert target["is_alive"] is False
assert hunter["global_goal_achieved"] is True or completed_objective("HUNT_TARGET")
```

---

# 16. Why `kill_stalker` is a good advanced benchmark

It requires:

```text
Memory:
  where was the target last seen?
  where does the target usually go?
  where can I buy ammo?

Belief:
  is target still alive?
  how confident am I about target location?
  am I ready?

Needs:
  do I need food/water/heal before hunt?
  do I need ammo/weapon/armor?

Objectives:
  prepare
  locate
  hunt
  retreat
  heal
  shelter

ActivePlan:
  multi-step plan
  pause for survival/emission
  repair if target moves
  complete if target dies

BrainTrace:
  explain why NPC did not immediately attack
  explain why it bought ammo first
  explain why it resumed hunt after emission
```

This makes it a strong future integration benchmark for the whole NPC Brain v3 architecture.

---

# 17. Final recommendation

The artifact/weapon scenario is the best first PR5 E2E test because it is complex but still deterministic.

The `kill_stalker` scenario is a better second benchmark because it requires:

```text
target tracking
combat readiness
risk assessment
memory confidence
plan repair when target moves
```

Recommended order:

```text
1. PR5 E2E: resupply weapon via artifact, with thirst/emission interruptions.
2. PR5 E2E: kill target with preparation, tracking, emission pause and plan repair.
```
