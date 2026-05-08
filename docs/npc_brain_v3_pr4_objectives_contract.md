# NPC Brain v3 — PR 4 Implementation Contract

> Проект: `zone_stalkers`  
> Предыдущие этапы:
>
> - PR 1: `PlanMonitor`, `brain_trace`, sleep partial progress, dynamic sleep duration, early wake-up, survival-safe rest.
> - PR 2: `ImmediateNeed`, `ItemNeed`, `NeedEvaluationResult`, liquidity, survival-mode purchasing.
> - PR 3: `MemoryStore v3`, `BeliefState` adapter, memory retrieval, `brain_trace.memory_used`.
>
> Цель PR 4: ввести `Objective` как центральную модель выбора цели и заменить разрозненную конкуренцию `NeedScores`/`Intent`/special cases на объяснимый objective scoring.

---

## 1. Зачем нужен PR 4

После PR 3 у нас уже есть:

```text
что NPC видит/помнит       → BeliefState
что NPCу срочно нужно      → ImmediateNeed / ItemNeed / NeedEvaluationResult
что NPC сейчас делает      → scheduled_action + brain_trace
что NPC помнит             → MemoryStore v3
```

Но ещё нет центрального ответа:

```text
Как NPC выбирает между несколькими целями?
```

Например:

```text
купить оружие
искать деньги
продать артефакт
идти за водой
спать
продолжать текущий путь
```

Сейчас это всё ещё частично живёт в:

```text
NeedScores
select_intent()
planner.py
tick_rules.py
PlanMonitor
legacy helper functions
```

PR 4 должен ввести промежуточный слой:

```text
Drive / NeedEvaluationResult / BeliefState
  → Objective candidates
  → Objective scoring
  → selected Objective
  → Intent compatibility layer
  → existing planner
```

Это ещё не полный отказ от `Intent`, но это важный шаг к новой архитектуре.

---

## 2. Цель PR 4

После PR 4 NPC должен уметь:

1. Сгенерировать список `Objective` candidates.
2. Оценить каждый objective по единой score-модели.
3. Показать в `brain_trace`, почему выбран objective.
4. Показать top rejected alternatives.
5. Сравнить текущий активный план/действие с новым objective.
6. Не переключаться без достаточной причины.
7. Сохранять совместимость со старым `Intent`/`Plan`/`scheduled_action`.

---

## 3. Non-goals for PR 4

PR 4 НЕ делает:

- `ActivePlan` source of truth;
- полную замену `scheduled_action`;
- pause/adapt lifecycle;
- полноценный GOAP;
- сложный HTN planner;
- Redis/vector search;
- групповую стратегию;
- глубокую социальную дипломатию;
- полную замену всех legacy helper functions;
- полное удаление `NeedScores`;
- полное удаление `Intent`.

PR 4 — это scoring/decision layer над существующим planner.

---

## 4. Новые сущности

### 4.1. `Objective`

Рекомендуемый файл:

```text
backend/app/games/zone_stalkers/decision/models/objective.py
```

Модель:

```python
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Objective:
    key: str
    source: str                     # immediate_need | item_need | global_goal | environment | active_plan
    urgency: float                  # 0..1
    expected_value: float           # 0..1
    risk: float                     # 0..1
    time_cost: float                # normalized 0..1
    resource_cost: float            # normalized 0..1
    confidence: float               # 0..1
    goal_alignment: float           # 0..1
    memory_confidence: float        # 0..1
    target: dict[str, Any] | None = None
    required_capabilities: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 4.2. `ObjectiveScore`

```python
@dataclass(frozen=True)
class ObjectiveScore:
    objective_key: str
    raw_score: float
    final_score: float
    factors: tuple[dict[str, Any], ...]
    penalties: tuple[dict[str, Any], ...]
    decision: str | None = None  # selected | rejected | continue_current | blocked
```

### 4.3. `ObjectiveDecision`

```python
@dataclass(frozen=True)
class ObjectiveDecision:
    selected: Objective
    selected_score: ObjectiveScore
    alternatives: tuple[tuple[Objective, ObjectiveScore], ...]
    continue_current_score: ObjectiveScore | None = None
    switch_decision: str = "new_objective"  # continue_current | switch | new_objective
    reason: str = ""
```

### 4.4. `ObjectiveGenerationContext`

```python
@dataclass(frozen=True)
class ObjectiveGenerationContext:
    agent_id: str
    world_turn: int
    belief_state: BeliefState
    need_result: NeedEvaluationResult
    active_plan_summary: dict | None
    personality: dict
```

---

## 5. Objective keys for PR 4

PR 4 должен поддержать не все будущие цели, а минимально нужный набор.

### Survival / immediate

```text
RESTORE_WATER
RESTORE_FOOD
HEAL_SELF
REST
```

### Resupply / equipment

```text
RESUPPLY_FOOD
RESUPPLY_DRINK
RESUPPLY_MEDICINE
RESUPPLY_WEAPON
RESUPPLY_ARMOR
RESUPPLY_AMMO
GET_MONEY_FOR_RESUPPLY
```

### Environment

```text
REACH_SAFE_SHELTER
WAIT_IN_SHELTER
ESCAPE_DANGER
```

### Global goals

```text
FIND_ARTIFACTS
SELL_ARTIFACTS
HUNT_TARGET
SEARCH_INFORMATION
LEAVE_ZONE
IDLE
```

### Current plan

```text
CONTINUE_CURRENT_PLAN
```

---

## 6. Objective generator

Рекомендуемый файл:

```text
backend/app/games/zone_stalkers/decision/objectives/generator.py
```

API:

```python
def generate_objectives(ctx: ObjectiveGenerationContext) -> list[Objective]:
    ...
```

### 6.1. From ImmediateNeed

```text
drink_now → RESTORE_WATER
eat_now   → RESTORE_FOOD
heal_now  → HEAL_SELF
```

Example:

```python
Objective(
    key="RESTORE_WATER",
    source="immediate_need",
    urgency=need.urgency,
    expected_value=1.0,
    risk=0.05,
    time_cost=0.05 if item in inventory else estimated_travel_time,
    resource_cost=0.0 if item in inventory else estimated_price_cost,
    confidence=1.0,
    goal_alignment=0.8,
    memory_confidence=1.0,
    reasons=("Жажда критична", "Вода есть в инвентаре"),
)
```

### 6.2. From ItemNeed

```text
weapon item need → RESUPPLY_WEAPON
ammo item need   → RESUPPLY_AMMO
food stock       → RESUPPLY_FOOD
drink stock      → RESUPPLY_DRINK
```

If unaffordable:

```text
also generate GET_MONEY_FOR_RESUPPLY
```

### 6.3. From Environment

Emission:

```text
emission threat + unsafe location → REACH_SAFE_SHELTER
safe location during emission     → WAIT_IN_SHELTER
```

Combat:

```text
low hp / active combat → ESCAPE_DANGER or HEAL_SELF
```

### 6.4. From Global Goal

Depending on agent `global_goal`:

```text
get_rich              → FIND_ARTIFACTS / SELL_ARTIFACTS
kill_stalker          → HUNT_TARGET
unravel_zone_mystery  → SEARCH_INFORMATION
leave_zone            → LEAVE_ZONE
```

### 6.5. From current plan

If agent has active scheduled action / active_plan_v3:

```text
generate CONTINUE_CURRENT_PLAN
```

This objective represents the value of not switching.

---

## 7. Objective scoring

Рекомендуемый файл:

```text
backend/app/games/zone_stalkers/decision/objectives/scoring.py
```

### 7.1. Base formula

```text
final_score =
    urgency              * 0.35
  + expected_value       * 0.20
  + confidence           * 0.10
  + memory_confidence    * 0.10
  + goal_alignment       * 0.10
  - risk                 * risk_sensitivity
  - time_cost            * 0.10
  - resource_cost        * 0.05
  - blocked_penalty
```

### 7.2. Risk sensitivity

```python
risk_tolerance = agent.get("risk_tolerance", 0.5)
risk_sensitivity = 1.0 - risk_tolerance
```

### 7.3. Switch cost

Switching away from current plan/action should require a meaningful advantage.

```text
switch_threshold = 0.10
```

If:

```text
best_new_score <= continue_current_score + switch_threshold
```

then:

```text
continue current
```

Unless the new objective is survival-blocking:

```text
RESTORE_WATER critical
RESTORE_FOOD critical
HEAL_SELF critical
REACH_SAFE_SHELTER
ESCAPE_DANGER
```

### 7.4. Blocking objectives

Some objectives are blocking:

```text
critical thirst
critical hunger
critical HP
emission danger
combat danger
```

Blocking objectives can ignore normal switch threshold.

### 7.5. Score factors

Every score must include readable factors.

Example:

```json
{
  "objective_key": "RESTORE_WATER",
  "final_score": 0.91,
  "factors": [
    {"key": "urgency", "label": "Жажда 90%", "value": 0.90, "weight": 0.35},
    {"key": "inventory", "label": "Вода есть в инвентаре", "value": 1.0, "weight": 0.20}
  ],
  "penalties": [
    {"key": "risk", "label": "Риск низкий", "value": 0.05}
  ]
}
```

---

## 8. Integration with existing Intent layer

PR 4 не должен сразу удалять `Intent`.

Ввести adapter:

```text
Objective → Intent
```

Рекомендуемый файл:

```text
backend/app/games/zone_stalkers/decision/objectives/intent_adapter.py
```

Mapping:

```python
OBJECTIVE_TO_INTENT = {
    "RESTORE_WATER": INTENT_SEEK_WATER,
    "RESTORE_FOOD": INTENT_SEEK_FOOD,
    "HEAL_SELF": INTENT_HEAL_SELF,
    "REST": INTENT_REST,
    "RESUPPLY_WEAPON": INTENT_RESUPPLY,
    "RESUPPLY_ARMOR": INTENT_RESUPPLY,
    "RESUPPLY_AMMO": INTENT_RESUPPLY,
    "RESUPPLY_FOOD": INTENT_RESUPPLY,
    "RESUPPLY_DRINK": INTENT_RESUPPLY,
    "RESUPPLY_MEDICINE": INTENT_RESUPPLY,
    "GET_MONEY_FOR_RESUPPLY": INTENT_GET_RICH,
    "REACH_SAFE_SHELTER": INTENT_FLEE_EMISSION,
    "WAIT_IN_SHELTER": INTENT_WAIT_IN_SHELTER,
    "FIND_ARTIFACTS": INTENT_GET_RICH,
    "SELL_ARTIFACTS": INTENT_SELL_ARTIFACTS,
    "HUNT_TARGET": INTENT_HUNT_TARGET,
    "SEARCH_INFORMATION": INTENT_SEARCH_INFORMATION,
    "LEAVE_ZONE": INTENT_LEAVE_ZONE,
    "IDLE": INTENT_IDLE,
}
```

The produced `Intent` should include:

```python
intent.objective_key = objective.key
intent.objective_score = score.final_score
intent.reason = objective.reasons summary
```

If current `Intent` dataclass cannot hold these fields, store in `Intent.metadata`.

---

## 9. How PR 4 changes the decision pipeline

Current simplified pipeline:

```text
AgentContext
→ NeedScores
→ select_intent()
→ build_plan()
```

PR 4 pipeline:

```text
AgentContext
→ BeliefState
→ NeedEvaluationResult
→ ObjectiveGenerationContext
→ generate_objectives()
→ score_objectives()
→ choose_objective()
→ objective_to_intent()
→ build_plan()
```

### Compatibility mode

Keep legacy select_intent as fallback:

```python
try:
    decision = choose_objective_v3(...)
    intent = objective_to_intent(decision.selected, decision.selected_score)
except Exception:
    intent = select_intent(...)
```

But tests should cover v3 path.

---

## 10. Plan continuation scoring

PR 4 should not implement full ActivePlan, but it should make current scheduled action visible to objective scoring.

### CONTINUE_CURRENT_PLAN

Generate objective:

```python
Objective(
    key="CONTINUE_CURRENT_PLAN",
    source="active_plan",
    urgency=current_plan_urgency,
    expected_value=remaining_value,
    risk=remaining_risk,
    time_cost=remaining_time,
    resource_cost=0.0,
    confidence=current_plan_confidence,
    goal_alignment=current_goal_alignment,
    reasons=("Текущий план ещё актуален",),
)
```

### For scheduled_action

If no real `ActivePlan` exists, derive minimal summary from:

```text
agent["scheduled_action"]
agent["current_goal"]
agent["brain_trace"]
```

### Continue vs switch

```text
continue current if:
  continue_score + switch_threshold >= best_new_score
```

Unless best new objective is blocking.

---

## 11. BrainTrace additions

Add:

```json
{
  "active_objective": {
    "key": "RESUPPLY_WEAPON",
    "score": 0.65,
    "source": "item_need",
    "reason": "Нет оружия"
  },
  "objective_scores": [
    {
      "key": "RESUPPLY_WEAPON",
      "score": 0.65,
      "decision": "selected",
      "reason": "Нет оружия"
    },
    {
      "key": "RESUPPLY_FOOD",
      "score": 0.55,
      "decision": "rejected",
      "reason": "Еда ниже приоритета, голод не критичен"
    }
  ],
  "alternatives": [
    {
      "key": "REST",
      "score": 0.46,
      "decision": "rejected",
      "reason": "Сонливость умеренная"
    }
  ]
}
```

Limits:

```text
objective_scores <= 5
alternatives <= 5
```

Do not dump all candidates.

---

## 12. Frontend scope

`AgentProfileModal` should show:

```text
Активная цель:
  Получить оружие — score 0.65

Почему:
  + Нет оружия
  + Броня уже есть
  - Денег недостаточно

Отвергнутые альтернативы:
  Пополнить еду — 0.55
  Отдых — 0.46
  Искать артефакты — 0.40
```

No complex UI yet.

---

## 13. Expected files

### New files

```text
backend/app/games/zone_stalkers/decision/models/objective.py
backend/app/games/zone_stalkers/decision/objectives/__init__.py
backend/app/games/zone_stalkers/decision/objectives/generator.py
backend/app/games/zone_stalkers/decision/objectives/scoring.py
backend/app/games/zone_stalkers/decision/objectives/selection.py
backend/app/games/zone_stalkers/decision/objectives/intent_adapter.py
```

### Changed files

```text
backend/app/games/zone_stalkers/decision/intents.py
backend/app/games/zone_stalkers/decision/planner.py
backend/app/games/zone_stalkers/decision/debug/brain_trace.py
backend/app/games/zone_stalkers/rules/tick_rules.py
frontend/src/games/zone_stalkers/ui/AgentProfileModal.tsx
```

### Tests

```text
backend/tests/decision/v3/test_objective_generation.py
backend/tests/decision/v3/test_objective_scoring.py
backend/tests/decision/v3/test_objective_selection.py
backend/tests/decision/v3/test_objective_intent_adapter.py
backend/tests/decision/v3/test_brain_trace_objectives.py
backend/tests/decision/v3/test_continue_vs_switch.py
```

---

## 14. Test plan

### Objective generation

```text
critical thirst → RESTORE_WATER
no weapon → RESUPPLY_WEAPON
no money for weapon → GET_MONEY_FOR_RESUPPLY
emission unsafe → REACH_SAFE_SHELTER
global_goal get_rich → FIND_ARTIFACTS / SELL_ARTIFACTS
```

### Scoring

```text
critical thirst beats sleepiness
weapon 0.65 beats food stock 0.55
risk-averse NPC penalizes dangerous artifact search
high-risk NPC accepts more dangerous anomaly search
```

### Continue vs switch

```text
minor food stock need does not interrupt almost-complete travel
critical thirst interrupts travel/sleep
emergency_flee remains protected
```

### Intent adapter

```text
RESTORE_WATER → seek_water
RESUPPLY_WEAPON → resupply with objective metadata
GET_MONEY_FOR_RESUPPLY → get_rich
```

### BrainTrace

```text
active_objective present
objective_scores limited to top 5
rejected alternatives readable
```

---

## 15. Definition of Done

PR 4 is done when:

- [ ] `Objective`, `ObjectiveScore`, `ObjectiveDecision` exist.
- [ ] Objectives are generated from `ImmediateNeed`, `ItemNeed`, environment and global goals.
- [ ] Objective scoring is deterministic.
- [ ] Continue-current-plan objective exists.
- [ ] Switch threshold prevents jitter.
- [ ] Blocking survival/environment objectives can override switch threshold.
- [ ] Objective → Intent adapter exists.
- [ ] Legacy `select_intent()` remains as fallback.
- [ ] `brain_trace` shows active objective and rejected alternatives.
- [ ] Frontend displays objective summary.
- [ ] Existing PR 1–3 tests remain green.

---

## 16. What remains after PR 4

After PR 4, the project has:

```text
BeliefState
MemoryStore
ImmediateNeed / ItemNeed
Objective scoring
Intent compatibility
brain_trace alternatives
```

But it still does not have:

```text
ActivePlan as source of truth
pause/adapt lifecycle
plan repair as general mechanism
scheduled_action as pure runtime detail
```

That is PR 5.

---

## 17. Final position

PR 4 is the point where NPC decision-making becomes systematically explainable.

After PR 4, `NeedScores` and `Intent` are compatibility layers, not the conceptual center.

The conceptual center becomes:

```text
BeliefState + NeedEvaluationResult + Objective scoring
```
