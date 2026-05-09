# NPC Brain v3 — PR 4 Final Fixes from Post-PR4 NPC Logs

> Context: after frontend rebuild and review of:
>
> - `stalker_Чел_1_history.json`
> - `stalker_Чел_1_full_debug.json`
>
> Current status:
>
> ```text
> PR 4 changes are visible and mostly working:
> - objective_decision memory exists;
> - objectives are primary;
> - intent is adapter/execution bridge;
> - compact NPC history export works;
> - minor RESTORE_FOOD/WATER → wait regression is not visible.
> ```
>
> Remaining issues before closing PR 4:
>
> ```text
> 1. Selected RESUPPLY_* objective does not always constrain planner category.
> 2. REST soft pressure is still marked as immediate_need.
> 3. Compact export/profile loses current objective during plan_monitor continuation.
> ```

---

# 1. Fix: selected `RESUPPLY_*` objective must constrain planner category

## Problem

The log shows this sequence:

```text
objective = RESUPPLY_FOOD
reason = Недостаточный запас еды
adapter intent = resupply
plan_step = trade_buy_item
```

But the next trade decision buys water:

```text
trade_decision item_type = water
reason = buy_drink_resupply
```

Only after that does the NPC buy bread.

This means the selected objective is not fully preserved through:

```text
Objective → Intent adapter → Planner
```

Current behavior is effectively:

```text
RESUPPLY_FOOD
→ generic resupply intent
→ planner re-evaluates item needs
→ buys whichever item need wins internally
```

This breaks the PR 4 invariant:

```text
Objective is the reason.
Intent is only the execution bridge.
```

If the selected objective is `RESUPPLY_FOOD`, the first resupply plan should target food.

## Required change

Carry objective category through the adapter into the planner.

Recommended mapping:

```text
RESUPPLY_WEAPON   → forced_resupply_category = weapon
RESUPPLY_ARMOR    → forced_resupply_category = armor
RESUPPLY_AMMO     → forced_resupply_category = ammo
RESUPPLY_FOOD     → forced_resupply_category = food
RESUPPLY_DRINK    → forced_resupply_category = drink
RESUPPLY_MEDICINE → forced_resupply_category = medicine
```

## Implementation options

### Option A — add metadata to Intent

Preferred.

In `objective_to_intent(...)`, for resupply objectives:

```python
Intent(
    kind="resupply",
    score=score.total,
    reason=objective.reason,
    metadata={
        "objective_key": objective.key,
        "forced_resupply_category": "food",
    },
)
```

Then in planner:

```python
forced_category = intent.metadata.get("forced_resupply_category")
```

If present, planner should use that category instead of reselecting dominant item need.

### Option B — add specialized resupply intent kinds

Less preferred but acceptable:

```text
resupply_food
resupply_drink
resupply_ammo
resupply_medicine
resupply_weapon
resupply_armor
```

Then map them internally to category.

### Option C — use objective key from `_v2_context`

Not recommended. It creates hidden coupling.

## Planner rule

When `forced_resupply_category` exists:

```text
1. Find matching ItemNeed category.
2. Build plan for that exact category.
3. If unavailable/unaffordable:
   - return meaningful fallback for that objective;
   - or reject objective with plan_unavailable.
4. Do not silently buy a different category.
```

Example:

```text
RESUPPLY_FOOD selected
→ buy bread/canned_food/military_ration
→ never water
```

Exception:

```text
If the selected category is impossible and the next objective is RESUPPLY_DRINK,
that should happen through objective fallback/reselection,
not inside the same RESUPPLY_FOOD plan.
```

## Required tests

### Test 1 — food objective buys food

```python
def test_resupply_food_objective_forces_food_purchase():
    ...
```

Setup:

```text
selected objective = RESUPPLY_FOOD
trader has water and bread
NPC needs both food and drink
```

Expected:

```text
first plan step = trade_buy_item
item category/type is food: bread/canned_food/military_ration
not water
```

### Test 2 — drink objective buys drink

```python
def test_resupply_drink_objective_forces_drink_purchase():
    ...
```

Expected:

```text
RESUPPLY_DRINK → water/purified_water
not bread
```

### Test 3 — ammo objective buys compatible ammo

```python
def test_resupply_ammo_objective_forces_ammo_purchase():
    ...
```

Expected:

```text
RESUPPLY_AMMO → ammo compatible with equipped weapon
```

### Test 4 — unavailable forced category rejects objective

```python
def test_forced_resupply_category_unavailable_rejects_objective_not_wrong_category():
    ...
```

Setup:

```text
RESUPPLY_FOOD selected
trader has water but no food
```

Expected:

```text
RESUPPLY_FOOD rejected or plan_unavailable
planner does not buy water under RESUPPLY_FOOD
```

## Priority

```text
BLOCKER for closing PR 4
```

---

# 2. Fix: `REST` soft pressure should not be marked as `immediate_need`

## Problem

The log shows:

```text
objective = REST
objective_source = immediate_need
reason = Усталость растёт
top pressures: get_rich 39%, eat 38%, sleep 34%
plan_step = sleep_for_hours
```

Sleep pressure around `34%` is not an immediate/survival need.

This is the same class of issue previously fixed for food/water:

```text
soft pressure should not be labeled as immediate_need
```

## Required change

Separate rest sources:

```text
critical sleep / unsafe exhaustion:
  source = immediate_need

normal growing sleepiness:
  source = soft_need

rest for HP/radiation recovery:
  source = recovery_need
```

Recommended:

```text
REST from sleepiness only:
  source = soft_need

REST from low HP / radiation recovery:
  source = recovery_need

REST from critical exhaustion:
  source = immediate_need
```

## Suggested thresholds

Reuse or define explicit constants:

```python
SOFT_REST_THRESHOLD = 50
CRITICAL_REST_THRESHOLD = 80
```

Behavior:

```text
sleepiness < SOFT_REST_THRESHOLD:
  do not generate REST from sleepiness alone
  unless recovery_need is active

SOFT_REST_THRESHOLD <= sleepiness < CRITICAL_REST_THRESHOLD:
  generate REST with source = soft_need
  not blocking

sleepiness >= CRITICAL_REST_THRESHOLD:
  generate REST with source = immediate_need
  possibly blocking
```

Recovery exception:

```text
If HP is low or radiation is meaningful and location is safe,
REST can be generated as recovery_need even if sleepiness is moderate.
```

But then reason must say:

```text
Восстановление после ранений / снятие радиации
```

not only:

```text
Усталость растёт
```

## Prevent early strategic interruption

If `REST` is soft and strategic objective is executable:

```text
REST should not beat FIND_ARTIFACTS / GET_MONEY_FOR_RESUPPLY
unless its score advantage is significant
or sleepiness is above threshold.
```

The existing maintenance-vs-strategic margin may already handle part of this, but only if REST is correctly classified as non-blocking maintenance.

## Required tests

### Test 1 — low sleepiness is not immediate

```python
def test_rest_low_sleepiness_is_not_immediate_need():
    ...
```

Setup:

```text
sleepiness = 34
hp = 100
radiation = 0
safe location
```

Expected:

```text
REST either not generated
or generated as source = soft_need only if above configured soft threshold
not source = immediate_need
```

### Test 2 — soft rest does not interrupt strategic goal too early

```python
def test_soft_rest_does_not_beat_executable_strategic_goal_by_small_margin():
    ...
```

Setup:

```text
sleepiness = 34-45
global_goal = get_rich
FIND_ARTIFACTS executable
```

Expected:

```text
selected objective = FIND_ARTIFACTS / GET_MONEY_FOR_RESUPPLY
not REST
```

### Test 3 — critical exhaustion still wins

```python
def test_critical_sleepiness_selects_rest():
    ...
```

Setup:

```text
sleepiness = 85+
```

Expected:

```text
REST selected
source = immediate_need
```

### Test 4 — recovery rest is separate

```python
def test_recovery_rest_uses_recovery_need_source():
    ...
```

Setup:

```text
hp low or radiation high
sleepiness moderate
safe location
```

Expected:

```text
REST source = recovery_need
reason mentions recovery/radiation/hp
```

## Priority

```text
HIGH
```

This is not as severe as the wrong resupply category, but it should be fixed before PR 4 closes because PR 4 is specifically about clean objective semantics.

---

# 3. Fix: compact export/profile should keep current objective during `plan_monitor`

## Problem

In compact export:

```json
"active_objective": null,
"adapter_intent": {},
"latest_decision": {
  "summary": "Продолжаю travel — action_still_valid."
}
```

This happens because the latest brain_trace events are all `plan_monitor` continuation events:

```text
Продолжаю travel — action_still_valid
```

But full debug still has current objective context in `_v2_context`:

```text
objective_key = FIND_ARTIFACTS
intent_kind = get_rich
plan_step_0 = travel_to_location
```

So the compact export/profile makes it look like the NPC has no current objective, although it is continuing a travel action that came from `FIND_ARTIFACTS`.

## Required UX distinction

The frontend/export should distinguish:

```text
latest_event:
  latest brain_trace event, including plan_monitor

latest_decision:
  latest event with mode = decision

current_runtime:
  scheduled_action / plan_monitor continuation

current_objective:
  currently known objective from:
    1. latest decision event active_objective
    2. agent._v2_context.objective_key
    3. latest objective_decision in story_timeline
```

## Required change in compact export

In `exportNpcHistory.ts`, do not use the latest trace event as `latest_decision` if it is only `plan_monitor`.

Add helpers:

```ts
export const getLatestTraceEvent = (trace?: BrainTrace | null): BrainTraceEvent | null => {
  if (!trace?.events?.length) return null;
  return trace.events[trace.events.length - 1] ?? null;
};

export const getLatestDecisionEvent = (trace?: BrainTrace | null): BrainTraceEvent | null => {
  if (!trace?.events?.length) return null;
  const decisions = trace.events.filter((ev) => ev.mode === 'decision');
  return decisions.length ? decisions[decisions.length - 1] : null;
};
```

Add current objective resolver:

```ts
const getCurrentObjectiveFromAgent = (
  agent: AgentForProfile,
  latestDecision: BrainTraceEvent | null,
  storyTimeline: CompactTimelineEntry[],
): BrainTraceObjectiveInfo | null => {
  if (latestDecision?.active_objective) return latestDecision.active_objective;

  const v2ObjectiveKey = agent._v2_context?.objective_key;
  if (v2ObjectiveKey) {
    return {
      key: v2ObjectiveKey,
      score: agent._v2_context?.objective_score ?? agent._v2_context?.intent_score ?? 0,
      source: 'current_context',
      reason: agent._v2_context?.objective_reason ?? agent._v2_context?.intent_reason ?? undefined,
    };
  }

  const lastObjectiveMemory = [...storyTimeline]
    .reverse()
    .find((entry) => entry.objective_key);

  if (lastObjectiveMemory?.objective_key) {
    return {
      key: lastObjectiveMemory.objective_key,
      score: 0,
      source: 'last_objective_decision',
      reason: lastObjectiveMemory.summary,
    };
  }

  return null;
};
```

Extend exported schema:

```ts
npc_brain: {
  current_thought?: string;
  latest_event?: BrainTraceEvent | null;
  latest_decision?: ... | null;
  current_objective?: BrainTraceObjectiveInfo | null;
  current_runtime?: {
    mode: 'scheduled_action' | 'idle';
    scheduled_action?: unknown;
    latest_plan_monitor?: BrainTraceEvent | null;
  };
}
```

## Required UI change

In `NpcBrainPanel`:

If latest event is `plan_monitor`:

```text
Сейчас:
  Продолжаю travel — action_still_valid

Текущая objective:
  FIND_ARTIFACTS

Последнее решение:
  FIND_ARTIFACTS → get_rich → travel_to_location
```

Do not display:

```text
active objective: null
adapter_intent: {}
```

## Required tests/manual checks

### Manual check 1 — scheduled travel

Setup:

```text
NPC has scheduled_action travel
latest brain_trace events are plan_monitor
_v2_context.objective_key = FIND_ARTIFACTS
```

Expected compact export:

```text
agent.active_objective or npc_brain.current_objective = FIND_ARTIFACTS
npc_brain.latest_event.mode = plan_monitor
npc_brain.latest_decision is null or previous decision
current_runtime.scheduled_action.type = travel
```

### Manual check 2 — no decision history

If there is no decision history:

```text
current_objective = null
UI says "NPC continues action, objective context unavailable"
```

### Manual check 3 — profile modal

In profile, while NPC is travelling:

```text
NPC Brain v3 panel shows:
- current runtime action
- current objective if known
- not an empty adapter_intent object
```

## Priority

```text
MEDIUM/HIGH
```

This does not break NPC behavior, but it directly affects the new PR 4 debug UX.

---

# 4. Summary checklist

Before closing PR 4:

```text
[ ] RESUPPLY_* objective category survives Objective → Intent → Planner.
[ ] RESUPPLY_FOOD cannot buy water.
[ ] RESUPPLY_DRINK cannot buy bread.
[ ] RESUPPLY_AMMO buys compatible ammo.
[ ] Unavailable forced category becomes plan_unavailable/fallback, not wrong-category purchase.
[ ] REST soft pressure is not source=immediate_need.
[ ] REST has threshold/gating or recovery_need distinction.
[ ] Compact export distinguishes latest_event, latest_decision, current_objective, current_runtime.
[ ] Profile does not show empty active_objective during plan_monitor if _v2_context has objective_key.
[ ] Tests/manual checks added for all above.
```

---

# 5. Expected final behavior after fixes

## Resupply

```text
Selected objective:
  RESUPPLY_FOOD

Execution:
  resupply adapter with forced_resupply_category=food

Planner:
  buy bread/canned_food/military_ration

Never:
  buy water under RESUPPLY_FOOD
```

## Rest

```text
sleepiness = 34:
  no immediate REST

sleepiness = 55:
  REST may appear as soft_need

sleepiness = 85:
  REST appears as immediate_need

low HP / radiation:
  REST appears as recovery_need
```

## Compact profile/export during travel

```text
Current runtime:
  travel, action_still_valid

Current objective:
  FIND_ARTIFACTS

Adapter:
  get_rich

Scheduled action:
  travel to loc_S5, final target loc_S3
```

---

# 6. What is not required now

Do not implement PR 5 features here:

```text
ActivePlan source of truth
pause/resume/repair
step lifecycle
plan memory refs
target moved repair
confirmed_empty plan repair
```

These remain for PR 5.

