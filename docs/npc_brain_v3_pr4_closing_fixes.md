# NPC Brain v3 — PR 4 Closing Fixes: Objective Layer Consistency

> Branch: `copilot/implement-pr-4-npc-brain-v3`  
> Goal: подготовить PR 4 к закрытию после анализа NPC-лога по окончанию PR 4.
>
> Главная проблема из лога:
>
> ```text
> PR 4 objective-layer уже работает, но minor RESTORE_FOOD / RESTORE_WATER
> иногда выигрывают у более системной цели GET_MONEY_FOR_RESUPPLY.
> ```
>
> Из-за этого NPC в начале симуляции начинает "тупить":
>
> ```text
> seek_water 36% → plan_step = wait
> seek_food 41% → plan_step = wait
> потом сон / поездка к торговцу за едой/водой
> вместо раннего GET_MONEY_FOR_RESUPPLY / поиска артефактов
> ```
>
> Этот документ описывает системный fix: не просто подкрутить веса, а довести PR 4 до целостной архитектуры, где Objective layer не воспроизводит старые рудименты NeedScores/Intent.

---

# 1. Что уже работает в PR 4

В логах уже видны признаки PR 4:

```text
active_objective
objective_scores
alternatives
memory_used
objective_key в _v2_context
```

Это значит:

```text
Objective layer реально подключён к runtime trace.
```

Пример из лога:

```text
active_objective = RESTORE_FOOD
score = 0.404

alternatives:
  GET_MONEY_FOR_RESUPPLY = 0.372
  RESTORE_WATER = 0.369
  REST = 0.354
  FIND_ARTIFACTS = 0.176
```

Проблема не в том, что PR 4 не работает.  
Проблема в том, что PR 4 пока слишком охотно превращает слабые hunger/thirst scores в полноценные objectives.

---

# 2. Главная архитектурная ошибка

Сейчас PR 4 повторно ввёл проблему, которую PR 2 уже частично решил.

В PR 2 была введена граница:

```text
hunger/thirst below soft consume threshold
→ не тратить consumable
→ не делать бессмысленный seek_food / seek_water → wait
```

Но PR 4 generator снова создаёт objectives:

```text
RESTORE_FOOD
RESTORE_WATER
```

почти от любого `NeedScores.eat/drink > 0.05`.

В итоге:

```text
ObjectiveGenerator:
  "Голод 29–41%? Создам RESTORE_FOOD."

Scoring:
  "RESTORE_FOOD дешёвый, понятный, с памятью — пусть выиграет."

IntentAdapter:
  "RESTORE_FOOD → seek_food."

Planner:
  "Голод ниже soft threshold, consumable тратить нельзя.
   Конкретного действия нет."

Runtime:
  wait / repeated decision / поездка за хлебом вместо главной цели.
```

Это не локальная ошибка одного веса.  
Это нарушение инварианта:

```text
Objective should be actionable, or clearly marked as non-actionable and not selected.
```

---

# 3. Новый PR 4 invariant

Добавить и соблюдать инвариант:

```text
Objective can be selected only if:
  1. it is blocking/survival/emergency;
  OR
  2. it is above its actionability threshold;
  OR
  3. it has a concrete executable plan;
  OR
  4. it is an explicit fallback objective like IDLE.
```

Иначе objective должна быть:

```text
not generated
or generated with blocker
or rejected during plan feasibility validation
```

---

# 4. Required fix A — do not generate low-value RESTORE_FOOD / RESTORE_WATER

## Problem

In `backend/app/games/zone_stalkers/decision/objectives/generator.py`, non-immediate food/water objectives are generated from generic scores:

```python
if not any(o.key == OBJECTIVE_RESTORE_WATER for o in result) and float(need_result.scores.drink) > 0.05:
    ...
```

and:

```python
if not any(o.key == OBJECTIVE_RESTORE_FOOD for o in result) and float(need_result.scores.eat) > 0.05:
    ...
```

This makes almost any mild hunger/thirst compete as a real objective.

## Required change

Use the same soft thresholds as PR 2 planner:

```python
SOFT_RESTORE_FOOD_THRESHOLD = 50
SOFT_RESTORE_DRINK_THRESHOLD = 40
```

Recommended location:

```text
backend/app/games/zone_stalkers/decision/objectives/constants.py
```

or reuse a shared decision constant if one already exists.

### Rule

Generate fallback `RESTORE_FOOD` only if:

```text
hunger >= SOFT_RESTORE_FOOD_THRESHOLD
```

Generate fallback `RESTORE_WATER` only if:

```text
thirst >= SOFT_RESTORE_DRINK_THRESHOLD
```

ImmediateNeed branch remains unchanged:

```text
ImmediateNeed survival
ImmediateNeed rest_preparation
ImmediateNeed healing
```

must still create blocking/urgent objectives.

## Pseudocode

```python
hunger = int(ctx.personality.get("hunger") or 0)
thirst = int(ctx.personality.get("thirst") or 0)

can_generate_soft_food_restore = hunger >= SOFT_RESTORE_FOOD_THRESHOLD
can_generate_soft_water_restore = thirst >= SOFT_RESTORE_DRINK_THRESHOLD
```

Then gate the fallback branches.

## Expected effect

For the log case:

```text
hunger = 29
thirst = 15
money = 329
weapon = null
```

Expected selected objective:

```text
GET_MONEY_FOR_RESUPPLY
```

not:

```text
RESTORE_FOOD
RESTORE_WATER
```

---

# 5. Required fix B — separate `immediate_need` from `soft_need`

## Problem

Fallback RESTORE_FOOD/WATER are currently created with:

```python
source="immediate_need"
```

even when they are not ImmediateNeed records.

This makes trace misleading:

```text
active_objective.source = immediate_need
```

while hunger/thirst are non-critical.

## Required change

Immediate branch:

```text
source="immediate_need"
```

Fallback soft hunger/thirst branch:

```text
source="soft_need"
```

or:

```text
source="need_score"
```

Recommended:

```python
source="soft_need"
```

## Trace rule

Trace should say:

```text
RESTORE_FOOD from soft_need
```

only when hunger is above soft restore threshold.

For critical/survival:

```text
RESTORE_FOOD from immediate_need
```

---

# 6. Required fix C — dynamic expected_value for RESTORE_FOOD/WATER

## Problem

Current fallback food/water objectives use nearly constant high expected value:

```text
RESTORE_WATER expected_value = 0.85
RESTORE_FOOD expected_value = 0.80
```

This is too high for mild hunger/thirst.

## Required change

Expected value should scale with actual need level.

Recommended formula:

```python
def _soft_need_value(value: int, threshold: int) -> float:
    if value < threshold:
        return 0.0
    return min(0.85, 0.35 + ((value - threshold) / max(1, 100 - threshold)) * 0.50)
```

Examples:

```text
hunger 29:
  objective not generated

hunger 50:
  expected_value ≈ 0.35

hunger 65:
  expected_value ≈ 0.50

hunger 85:
  expected_value ≈ 0.70

hunger 95:
  expected_value ≈ 0.80+
```

Critical ImmediateNeed can still use:

```text
expected_value = 1.0
```

---

# 7. Required fix D — memory must not bypass actionability threshold

## Problem

The trace says:

```text
Голод растёт; По памяти известен источник еды
```

This boosts the attractiveness of RESTORE_FOOD.

But remembering a food source should not make mild hunger urgent.

## Required rule

Memory can:

```text
- increase confidence;
- reduce time_cost;
- add source_refs;
```

Memory must not:

```text
- bypass hunger/thirst threshold;
- inflate urgency;
- make low need blocking;
- make non-actionable objective selected.
```

If hunger/thirst is below soft threshold:

```text
do not generate RESTORE_FOOD/WATER even if memory source exists.
```

---

# 8. Required fix E — objective-specific memory confidence

## Problem

`_memory_confidence(ctx)` currently averages `ctx.belief_state.relevant_memories`.

This is too noisy. It may include:

```text
semantic_travel_hop
semantic_travel_arrived
semantic_v2_decision
stalkers_seen
```

and then all objectives receive a similar memory score.

## Required change

Memory confidence should be objective-specific.

### For RESTORE_WATER

Use only memory records relevant to water/drink:

```text
water_source_known
item_bought water
trader_location_known with water stock
item_type water/purified_water
tags water/drink
```

### For RESTORE_FOOD

Use only:

```text
food_source_known
item_bought bread/canned_food
trader_location_known with food stock
item_type bread/canned_food
tags food
```

### For GET_MONEY_FOR_RESUPPLY

Use only:

```text
known anomaly/artifact source
known trader
artifact buyer
safe sell option
previous successful artifact sale
```

### For SELL_ARTIFACTS

Use only:

```text
trader_location_known
trader_buys_artifacts
previous artifact sale
```

## Implementation option

Replace global `_memory_confidence(ctx)` calls with helper:

```python
def _objective_memory_refs_and_confidence(
    ctx: ObjectiveGenerationContext,
    objective_key: str,
    category: str | None = None,
) -> tuple[tuple[str, ...], float]:
    ...
```

Return:

```text
source_refs
memory_confidence
```

If no specific memory exists:

```text
memory_confidence = 0.5
source_refs = ()
```

---

# 9. Required fix F — `memory_used` must align with selected objective

## Problem

In the log, selected objective says:

```text
По памяти известен источник еды
```

but `memory_used` contains mostly generic records:

```text
stalkers_seen
semantic_travel_hop
semantic_travel_arrived
semantic_v2_decision
```

This makes debug misleading.

## Required change

For decision trace:

```text
memory_used should prioritize selected_objective.source_refs
```

Specifically:

```text
1. Resolve selected_objective.source_refs that start with "memory:".
2. Put those records first in memory_used.
3. Set used_for based on selected objective:
   - RESTORE_FOOD → find_food
   - RESTORE_WATER → find_water
   - SELL_ARTIFACTS → find_trader / sell_artifacts
   - GET_MONEY_FOR_RESUPPLY → find_money_source / find_artifact_source
   - REACH_SAFE_SHELTER → avoid_threat / find_shelter
4. Only then append generic context memories if space remains.
```

Hard cap remains:

```text
max memory_used = 5
```

## Trace invariant

If reason says:

```text
По памяти известен источник еды
```

then `memory_used` must include a food-related memory.

If no food-related memory is present, do not include that reason.

---

# 10. Required fix G — plan feasibility validation after Objective → Intent → Plan

## Problem

The objective layer can select an objective, then adapter maps it to intent, then planner may fail or return a meaningless wait.

This should not be accepted silently.

## Required change

After:

```text
objective_to_intent
→ build_plan
```

validate whether the plan is meaningful for selected objective.

## Plan validity rules

A plan is meaningful if:

```text
- first step is not STEP_WAIT
OR
- selected objective is IDLE
OR
- selected objective is WAIT_IN_SHELTER
OR
- selected objective explicitly allows waiting
```

A plan is NOT meaningful if:

```text
selected objective = RESTORE_FOOD / RESTORE_WATER / GET_MONEY_FOR_RESUPPLY / SELL_ARTIFACTS
and plan is only wait
```

## Required behavior

If selected objective produces non-meaningful plan:

```text
1. Mark selected objective as rejected with blocker "plan_unavailable".
2. Try next alternative objective.
3. Repeat until meaningful plan is found.
4. If none found, fallback to IDLE.
```

## Pseudocode

```python
decision = choose_objective(objectives, personality=agent)

for objective, score in [selected] + alternatives:
    intent = objective_to_intent(objective, score, world_turn=world_turn)
    plan = build_plan(ctx, intent, state, world_turn, need_result)

    if _objective_plan_is_meaningful(objective, plan):
        selected_objective = objective
        selected_score = score
        selected_intent = intent
        selected_plan = plan
        break

    rejected_due_to_plan.append((objective, "plan_unavailable"))

else:
    selected_objective = idle_objective
    selected_intent = idle_intent
    selected_plan = idle_plan
```

## BrainTrace

Trace should show:

```text
RESTORE_FOOD rejected: plan_unavailable
GET_MONEY_FOR_RESUPPLY selected
```

---

# 11. Required fix H — objective layer should become primary in decision memory

## Problem

Legacy decision memory still uses:

```text
action_kind = v2_decision
intent_kind = seek_food
intent_score = 0.404
```

After PR 4, this is misleading.

The selected primary entity is no longer intent. It is objective.

Intent is now an execution adapter.

## Required change

Decision memory entries should become objective-first.

### New memory shape

```json
{
  "action_kind": "objective_decision",
  "objective_key": "GET_MONEY_FOR_RESUPPLY",
  "objective_score": 0.372,
  "objective_source": "item_need",
  "objective_reason": "Не хватает денег для обязательного пополнения",
  "adapter_intent_kind": "get_rich",
  "adapter_intent_score": 0.372,
  "plan_step": "travel_to_location",
  "plan_steps_count": 1
}
```

### Backward compatibility

If other code still expects `v2_decision`, either:

```text
Option A:
  keep action_kind="objective_decision" and add legacy fields:
    intent_kind
    intent_score

Option B:
  keep action_kind="v2_decision" for now but add objective fields.
```

Recommended for PR 4 closing:

```text
Option A if tests can be updated safely.
Option B if too risky, but trace/title must still be objective-first.
```

Minimum acceptable:

```text
memory summary and brain_trace current_thought should say:
  "Выбрана цель GET_MONEY_FOR_RESUPPLY ..."
not only:
  "Выбран intent get_rich ..."
```

---

# 12. Required fix I — objective-first current_goal update

## Problem

`current_goal` is still likely mapped from intent via `_INTENT_TO_GOAL`.

After PR 4, current goal should follow selected objective, not adapter intent.

Example:

```text
Objective = GET_MONEY_FOR_RESUPPLY
Adapter intent = get_rich
```

Current goal should preserve the actual reason:

```text
get_money_for_resupply
```

not only:

```text
get_rich
```

## Required change

Add objective-to-goal mapping:

```python
_OBJECTIVE_TO_GOAL = {
    "RESTORE_WATER": "restore_needs",
    "RESTORE_FOOD": "restore_needs",
    "HEAL_SELF": "emergency_heal",
    "REST": "restore_needs",
    "GET_MONEY_FOR_RESUPPLY": "get_money_for_resupply",
    "FIND_ARTIFACTS": "get_rich",
    "SELL_ARTIFACTS": "get_rich",
    "RESUPPLY_WEAPON": "resupply",
    "RESUPPLY_AMMO": "resupply",
    "REACH_SAFE_SHELTER": "emergency_shelter",
    "WAIT_IN_SHELTER": "emergency_shelter",
    "HUNT_TARGET": "kill_stalker",
    "PREPARE_FOR_HUNT": "prepare_for_hunt",
}
```

Keep intent mapping only as fallback.

---

# 13. Required fix J — avoid generic semantic decision memories as objective evidence

## Problem

`semantic_v2_decision` appears in `memory_used`.

That is usually not meaningful evidence for new decisions.

This can create feedback loops:

```text
I previously decided to seek food
→ memory says food is relevant
→ I decide to seek food again
```

## Required change

In retrieval / BeliefState relevant memory selection:

```text
Do not use semantic_v2_decision as source evidence for objectives.
```

It may remain in memory, but should be excluded from:

```text
objective source_refs
memory_confidence
objective-specific memory_used
```

Allowed uses:

```text
debug-only generic context
history panel
```

Not allowed:

```text
boosting RESTORE_FOOD / RESTORE_WATER / GET_MONEY_FOR_RESUPPLY
```

---

# 14. Required fix K — no low-priority maintenance ping-pong

## Problem

Low/medium objectives can ping-pong:

```text
RESTORE_FOOD
RESTORE_WATER
REST
RESTORE_FOOD
```

without completing higher-level direction.

PR 5 ActivePlan will solve this properly, but PR 4 should add a minimal anti-ping-pong guard.

## Minimal PR 4 rule

If selected objective is low-priority maintenance:

```text
RESTORE_FOOD
RESTORE_WATER
REST
```

and:

```text
not blocking
not critical
score advantage over best strategic objective < 0.10
```

then select strategic objective instead.

Strategic objectives:

```text
GET_MONEY_FOR_RESUPPLY
FIND_ARTIFACTS
SELL_ARTIFACTS
RESUPPLY_WEAPON
RESUPPLY_AMMO
HUNT_TARGET
SEARCH_INFORMATION
LEAVE_ZONE
```

Example from log:

```text
RESTORE_FOOD = 0.404
GET_MONEY_FOR_RESUPPLY = 0.372
difference = 0.032
RESTORE_FOOD is non-critical maintenance
```

Expected:

```text
GET_MONEY_FOR_RESUPPLY selected
```

because the maintenance advantage is too small.

---

# 15. Tests required before closing PR 4

## 15.1. Regression from uploaded NPC log

```python
def test_pr4_minor_food_water_do_not_block_get_money_for_resupply():
    ...
```

Setup:

```python
agent = {
    "money": 329,
    "equipment": {"weapon": None, "armor": leather_jacket},
    "inventory": [bandage, medkit, bread],
    "hunger": 29,
    "thirst": 15,
    "sleepiness": 12,
    "global_goal": "get_rich",
    "material_threshold": 5477,
}
```

Expected:

```text
selected objective = GET_MONEY_FOR_RESUPPLY
adapter intent = get_rich
not RESTORE_FOOD
not RESTORE_WATER
plan is travel/explore/get_rich, not wait
```

## 15.2. Old PR2 wait regression must stay fixed

```python
def test_pr4_soft_food_below_threshold_does_not_select_wait_plan():
    ...
```

Setup:

```text
hunger = 41
has bread
has no weapon
money insufficient
global_goal = get_rich
```

Expected:

```text
not RESTORE_FOOD
not seek_food → wait
```

## 15.3. Food above threshold can win

```python
def test_pr4_food_above_soft_threshold_can_restore_food():
    ...
```

Setup:

```text
hunger = 55
has bread
no emergency
```

Expected:

```text
RESTORE_FOOD selected
plan_step = consume_item
```

## 15.4. Water above threshold can win

```python
def test_pr4_water_above_soft_threshold_can_restore_water():
    ...
```

Setup:

```text
thirst = 45
has water
```

Expected:

```text
RESTORE_WATER selected
plan_step = consume_item
```

## 15.5. Critical still overrides everything

```python
def test_pr4_critical_thirst_still_blocks_get_money():
    ...
```

Setup:

```text
thirst = 95
global_goal = get_rich
```

Expected:

```text
RESTORE_WATER selected
metadata.is_blocking = true
```

## 15.6. Plan feasibility fallback

```python
def test_pr4_objective_with_wait_only_plan_is_rejected_for_next_objective():
    ...
```

Setup:

```text
RESTORE_FOOD would be top by raw score
but planner produces wait-only/non-actionable plan
GET_MONEY_FOR_RESUPPLY is second
```

Expected:

```text
RESTORE_FOOD rejected due plan_unavailable
GET_MONEY_FOR_RESUPPLY selected
```

## 15.7. Objective-first memory

```python
def test_pr4_decision_memory_is_objective_first():
    ...
```

Expected memory entry:

```text
action_kind = objective_decision
objective_key exists
adapter_intent_kind exists
```

or, if compatibility mode retained:

```text
action_kind = v2_decision
objective_key exists
objective_score exists
adapter_intent_kind exists
```

## 15.8. Memory used aligns with objective source refs

```python
def test_pr4_memory_used_matches_selected_objective_source_refs():
    ...
```

Setup:

```text
selected objective says food source known by memory
```

Expected:

```text
memory_used includes food-related memory
not only semantic_travel_hop / semantic_v2_decision
```

---

# 16. Acceptance criteria for closing PR 4

PR 4 is ready to close when:

```text
[ ] Objective layer is the primary decision source.
[ ] Intent is only an adapter/execution bridge.
[ ] Minor hunger/thirst below soft threshold do not create/select RESTORE objectives.
[ ] Non-critical RESTORE_FOOD/WATER use source="soft_need", not "immediate_need".
[ ] RESTORE expected_value scales with actual hunger/thirst.
[ ] Memory confidence is objective-specific, not generic average.
[ ] memory_used aligns with selected objective source_refs.
[ ] Plan feasibility validation rejects wait-only plans for actionable objectives.
[ ] Decision memory is objective-first or at least includes objective fields prominently.
[ ] current_goal is derived from objective, not only intent.
[ ] Maintenance objectives do not beat strategic objectives by tiny margins.
[ ] Regression from uploaded NPC log passes.
[ ] PR 1/2/3 test suites still pass.
[ ] PR 4 tests pass.
```

---

# 17. What should NOT be implemented in PR 4

Do not implement:

```text
ActivePlan source of truth
pause/resume/repair
long plan persistence
target tracking lifecycle
ambush/intercept
full TargetBelief
full hunt operation
social consequences
Redis/vector search
```

Those remain for PR 5 / post-PR5.

---

# 18. Recommended implementation order

1. Add shared PR4 objective thresholds/constants.
2. Gate fallback RESTORE_FOOD/WATER generation by thresholds.
3. Change fallback RESTORE source to `soft_need`.
4. Add dynamic expected_value for soft restore objectives.
5. Make memory confidence objective-specific.
6. Resolve selected objective source_refs into primary `memory_used`.
7. Add plan feasibility validation and fallback-to-next-objective.
8. Make decision memory objective-first.
9. Add objective-to-current-goal mapping.
10. Add anti-ping-pong margin for non-blocking maintenance objectives.
11. Add tests from section 15.
12. Run full test suite.

---

# 19. Core mental model

Before PR 4:

```text
NeedScores → Intent → Plan
```

After PR 4:

```text
NeedEvaluationResult + BeliefState
→ Objective candidates
→ Objective scoring
→ Objective decision
→ Intent adapter
→ legacy planner
```

Therefore:

```text
Intent is no longer the reason.
Intent is how the selected objective is executed.
```

The trace, memory and current_goal should reflect this.

---

# 20. Final note

The uploaded NPC log is a successful sign that PR 4 is active, because the trace contains objective fields.

But it also shows that PR 4 must not blindly wrap legacy `eat/drink` scores into objectives.

The closing fix is:

```text
Make objectives actionable, thresholded and objective-first.
```
