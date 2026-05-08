# NPC Brain v3 — PR 5 Implementation Contract

> Проект: `zone_stalkers`  
> Предыдущие этапы:
>
> - PR 1: action monitoring, `brain_trace`, sleep progress.
> - PR 2: explicit needs, liquidity, survival purchasing.
> - PR 3: `MemoryStore v3`, `BeliefState`.
> - PR 4: `Objective` generation/scoring, continue-vs-switch.
>
> Цель PR 5: сделать `ActivePlan` источником истины для длительного поведения NPC.  
> `scheduled_action` должен стать только runtime-исполнением текущего `PlanStep`.

---

## 1. Зачем нужен PR 5

До PR 5 система всё ещё живёт в гибридном режиме:

```text
Objective chooses what NPC wants
Intent/Plan describes short plan
scheduled_action executes long action
PlanMonitor can abort scheduled_action
brain_trace explains decisions
```

Но source of truth для длительного поведения всё ещё часто:

```text
agent["scheduled_action"]
agent["action_queue"]
```

Это ограничивает:

- pause;
- adapt;
- resume;
- repair plan;
- explainability;
- plan lifecycle;
- long-term continuity.

PR 5 должен перевернуть ответственность:

```text
ActivePlan = source of truth
scheduled_action = current runtime detail
```

---

## 2. Цель PR 5

После PR 5 NPC должен:

1. Иметь `agent["active_plan_v3"]` как главный объект текущего плана.
2. Хранить objective, steps, current step, status, score, reasons.
3. Исполнять следующий `PlanStep` через runtime bridge.
4. Создавать/обновлять `scheduled_action` только из текущего step.
5. Уметь:
   - continue;
   - pause;
   - resume;
   - abort;
   - complete;
   - repair.
6. Показывать полный plan lifecycle в `brain_trace`.
7. Сохранить совместимость со старым `scheduled_action` во время перехода.

---

## 3. Non-goals for PR 5

PR 5 НЕ делает:

- сложный GOAP;
- динамический HTN planner;
- Redis;
- multiplayer group plan ownership;
- human plan editor;
- full UI timeline editor;
- удаление всех legacy helpers;
- удаление `scheduled_action`.

PR 5 делает `scheduled_action` runtime detail, но не удаляет его физически.

---

## 4. `ActivePlan` model

Рекомендуемый файл:

```text
backend/app/games/zone_stalkers/decision/models/active_plan.py
```

Модель:

```python
from dataclasses import dataclass, field
from typing import Any, Literal


PlanStatus = Literal[
    "active",
    "paused",
    "completed",
    "failed",
    "aborted",
]


@dataclass(frozen=True)
class ActivePlan:
    schema_version: int
    id: str
    objective_key: str
    objective_score: float
    status: PlanStatus
    steps: tuple[dict[str, Any], ...]
    current_step_index: int
    created_turn: int
    updated_turn: int
    last_evaluated_turn: int | None
    expected_total_cost: float
    expected_total_risk: float
    expected_total_value: float
    commitment_strength: float
    switch_cost: float
    memory_refs: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    debug_summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
```

Stored in agent:

```python
agent["active_plan_v3"] = serialize_active_plan(plan)
```

---

## 5. `PlanStep` v3

Existing `PlanStep` can be extended or wrapped.

Required fields:

```python
@dataclass(frozen=True)
class PlanStepV3:
    id: str
    kind: str
    payload: dict[str, Any]
    status: str                 # pending | running | completed | failed | skipped
    preconditions: tuple[dict[str, Any], ...]
    expected_effects: tuple[dict[str, Any], ...]
    cost: float
    risk: float
    duration_ticks: int
    interruptibility: str       # none | checkpointed | anytime
    checkpoint_policy: str      # none | every_tick | every_30_ticks | on_completion
    started_turn: int | None = None
    completed_turn: int | None = None
    failure_reason: str | None = None
```

---

## 6. Source of truth rule

After PR 5:

```text
ActivePlan is the source of truth for why NPC is doing something.
scheduled_action is the source of truth only for the current runtime timer/progress.
```

Rules:

```text
If active_plan_v3 exists and status=active:
  tick uses active_plan_v3.current_step_index to decide what should run.

If scheduled_action exists but active_plan_v3 is missing:
  legacy compatibility adapter creates a minimal ActivePlan snapshot.

If active_plan_v3 says current step is completed:
  scheduled_action must be cleared or advanced.

If scheduled_action contradicts active_plan_v3:
  ActivePlan wins unless compatibility fallback explicitly says legacy mode.
```

---

## 7. Plan lifecycle

### 7.1. Create

When PR 4 selects a new objective and builds a plan:

```text
ObjectiveDecision
→ build_plan()
→ ActivePlan
→ first step bridged to scheduled_action if needed
```

### 7.2. Continue

If objective scoring says continue current:

```text
ActivePlan remains active
current step continues
scheduled_action continues
```

### 7.3. Complete step

When current step completes:

```text
mark step completed
advance current_step_index
if next step exists:
  schedule/execute next step
else:
  status = completed
```

### 7.4. Pause

Pause means:

```text
current plan remains valid
runtime scheduled_action is cleared or suspended
another short objective runs
plan can resume later
```

Examples:

```text
drink water then resume travel
heal then resume sell_artifacts
wait during transient danger then resume
```

### 7.5. Abort

Abort means:

```text
plan no longer valid or too risky
clear scheduled_action
clear action_queue
status = aborted
write memory/trace
```

Examples:

```text
target item gone
remembered trader not there
route impossible
objective no longer relevant
```

### 7.6. Repair

Repair means:

```text
same objective remains,
but plan needs inserted steps.
```

Examples:

```text
sleep requires drink/eat before rest
travel route blocked → reroute
buy item unaffordable → sell safe item first
```

---

## 8. Plan continuity decision

Рекомендуемый файл:

```text
backend/app/games/zone_stalkers/decision/plans/continuity.py
```

API:

```python
def assess_plan_continuity(
    *,
    agent: dict,
    active_plan: dict | None,
    objective_decision: ObjectiveDecision,
    belief_state: BeliefState,
    world_turn: int,
) -> PlanContinuityDecision:
    ...
```

Model:

```python
@dataclass(frozen=True)
class PlanContinuityDecision:
    decision: str  # continue | pause | resume | abort | repair | replace | complete
    reason: str
    old_plan_id: str | None
    new_objective_key: str | None
    repair_steps: tuple[dict, ...] = ()
    clear_scheduled_action: bool = False
```

---

## 9. Plan repair

PR 5 should generalize repair logic that was previously local to sleep/rest.

### Repair examples

```text
REST objective:
  insert consume drink/food before sleep.

RESUPPLY_WEAPON:
  insert sell safe item before buy weapon if money insufficient.

TRAVEL:
  insert reroute if route changed.

FIND_ARTIFACTS:
  if target location confirmed empty, replace target location.
```

### Repair policy

```text
repair if objective still valid but plan step blocked.
abort if objective invalid.
replace if better objective wins by score.
pause if temporary blocking objective must be handled first.
```

---

## 10. Runtime bridge

Рекомендуемый файл:

```text
backend/app/games/zone_stalkers/decision/plans/runtime_bridge.py
```

API:

```python
def ensure_runtime_for_current_step(
    *,
    agent_id: str,
    agent: dict,
    active_plan: dict,
    state: dict,
    world_turn: int,
) -> list[dict]:
    ...
```

Responsibilities:

```text
PlanStep travel_to_location → scheduled_action travel
PlanStep sleep_for_hours    → scheduled_action sleep
PlanStep explore_location   → scheduled_action explore
PlanStep consume_item       → immediate executor call
PlanStep trade_buy_item     → immediate executor call
PlanStep trade_sell_item    → immediate executor call
PlanStep wait               → no-op or scheduled wait
```

### Important

Executors should not decide the strategic reason anymore.

They execute current step.

---

## 11. Compatibility adapter

Because old saves may have only `scheduled_action`:

```python
def retrofit_active_plan_from_scheduled_action(agent, world_turn) -> dict:
    ...
```

Example:

```text
scheduled_action.type = sleep
→ ActivePlan objective_key = REST
→ step = sleep_for_hours
→ status = active
```

For travel:

```text
scheduled_action.type = travel
→ objective_key = UNKNOWN_LEGACY_TRAVEL
→ step = travel_to_location
```

The trace should mark:

```text
source = legacy_retrofit
```

---

## 12. Interaction with PlanMonitor

After PR 5, `PlanMonitor` should monitor `ActivePlan`, not raw `scheduled_action`.

Transition:

```text
PR 5.0:
  PlanMonitor reads active_plan_v3 if present,
  falls back to scheduled_action.

PR 5.1:
  PlanMonitor becomes PlanContinuityMonitor.
```

Abort should update:

```text
active_plan_v3.status = aborted
scheduled_action = None
action_queue = []
```

Continue should update:

```text
active_plan_v3.last_evaluated_turn = world_turn
```

Pause should update:

```text
active_plan_v3.status = paused
paused_reason = ...
```

---

## 13. BrainTrace additions

Add:

```json
{
  "active_plan": {
    "id": "plan_123",
    "status": "active",
    "objective_key": "RESUPPLY_WEAPON",
    "current_step_index": 1,
    "steps": [
      {
        "index": 0,
        "kind": "travel_to_location",
        "status": "completed",
        "label": "Дойти до Бункера"
      },
      {
        "index": 1,
        "kind": "trade_buy_item",
        "status": "running",
        "label": "Купить оружие"
      }
    ]
  },
  "plan_decision": {
    "decision": "repair",
    "reason": "Недостаточно денег: добавляю продажу безопасного предмета"
  }
}
```

Limits:

```text
max displayed steps = 5
do not dump huge payloads
```

---

## 14. Frontend scope

Agent profile should show:

```text
Активный план:
  Цель: Получить оружие
  Статус: active
  Текущий шаг: Купить оружие

Шаги:
  ✓ Дойти до Бункера
  → Продать артефакт
  · Купить оружие

Решение по плану:
  repair — недостаточно денег, добавлена продажа
```

No visual graph editor.

---

## 15. Expected files

### New files

```text
backend/app/games/zone_stalkers/decision/models/active_plan.py
backend/app/games/zone_stalkers/decision/plans/__init__.py
backend/app/games/zone_stalkers/decision/plans/lifecycle.py
backend/app/games/zone_stalkers/decision/plans/continuity.py
backend/app/games/zone_stalkers/decision/plans/repair.py
backend/app/games/zone_stalkers/decision/plans/runtime_bridge.py
backend/app/games/zone_stalkers/decision/plans/compat.py
```

### Changed files

```text
backend/app/games/zone_stalkers/rules/tick_rules.py
backend/app/games/zone_stalkers/decision/planner.py
backend/app/games/zone_stalkers/decision/plan_monitor.py
backend/app/games/zone_stalkers/decision/debug/brain_trace.py
frontend/src/games/zone_stalkers/ui/AgentProfileModal.tsx
```

### Tests

```text
backend/tests/decision/v3/test_active_plan_model.py
backend/tests/decision/v3/test_active_plan_lifecycle.py
backend/tests/decision/v3/test_plan_repair.py
backend/tests/decision/v3/test_runtime_bridge.py
backend/tests/decision/v3/test_legacy_scheduled_action_retrofit.py
backend/tests/decision/v3/test_brain_trace_active_plan.py
backend/tests/decision/v3/test_pause_resume.py
```

---

## 16. Test plan

### Retrofit

```text
legacy scheduled_action sleep → active_plan_v3 REST
legacy scheduled_action travel → active_plan_v3 legacy travel
```

### Lifecycle

```text
step completion advances current_step_index
last step completion marks plan completed
abort marks plan aborted and clears scheduled_action
```

### Repair

```text
sleep plan with hunger/thirst high → insert consume steps
buy weapon unaffordable → insert sell/fallback step
route blocked → repair travel or abort if impossible
```

### Pause/resume

```text
travel to trader paused to drink water
after drink completed, travel resumes
```

### Runtime bridge

```text
travel step creates scheduled_action travel
sleep step creates scheduled_action sleep with PR1 interval fields
consume step executes immediately
trade step executes immediately
```

### BrainTrace

```text
active_plan summary present
plan_decision present
steps limited and readable
```

---

## 17. Definition of Done

PR 5 is done when:

- [ ] `ActivePlan` model exists and is serialized into `agent["active_plan_v3"]`.
- [ ] New objectives create ActivePlan.
- [ ] Current PlanStep drives `scheduled_action`.
- [ ] Legacy scheduled actions can be retrofitted.
- [ ] Step completion advances plan.
- [ ] Plan completion/abort updates status.
- [ ] Pause/resume works for at least one survival interruption case.
- [ ] Repair works for sleep preparation and unaffordable buy.
- [ ] PlanMonitor/continuity reads ActivePlan when available.
- [ ] `brain_trace.active_plan` and `brain_trace.plan_decision` are populated.
- [ ] Frontend displays active plan summary.
- [ ] Old saves with only scheduled_action still work.
- [ ] PR 1–4 tests remain green.

---

## 18. What remains after PR 5

After PR 5, the core NPC Brain v3 architecture is effectively in place:

```text
BeliefState
NeedEvaluationResult
Objective scoring
MemoryStore
ActivePlan
Plan lifecycle
Runtime scheduled_action bridge
brain_trace
```

Remaining future work becomes iterative improvement, not architectural migration:

```text
better objective scoring balance
better memory consolidation
more plan repair templates
group plans
social reasoning
Redis/PostgreSQL memory backend if needed
```

---

## 19. Final position

PR 5 is the point where the project can say:

```text
NPC Brain v3 is the primary behavior architecture.
```

After PR 5:

```text
scheduled_action is no longer the behavior architecture.
It is only the runtime execution detail of the current ActivePlan step.
```
