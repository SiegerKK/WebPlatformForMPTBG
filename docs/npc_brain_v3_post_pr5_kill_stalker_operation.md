# NPC Brain v3+ — Systemic `kill_stalker` / Hunt Operation Roadmap

> Назначение: документ описывает, как после PR 5 развить цель `kill_stalker` из простого intent/combat shortcut в полноценную системную операцию охоты.
>
> Контекст текущей архитектуры:
>
> ```text
> PR 1: PlanMonitor, brain_trace, sleep/scheduled_action safety
> PR 2: ImmediateNeed, ItemNeed, liquidity
> PR 3: MemoryStore v3, BeliefState
> PR 4: Objective generation/scoring
> PR 5: ActivePlan source of truth
> ```
>
> После PR 5 базовая цепочка будет:
>
> ```text
> Memory → BeliefState → NeedEvaluationResult → Objectives → ActivePlan → PlanStep → scheduled_action/runtime bridge
> ```
>
> Этот документ описывает следующий слой: как сделать `kill_stalker` не действием, а полноценной охотничьей операцией.

---

# 1. Главная идея

`kill_stalker` не должен быть одним действием.

Неправильно:

```text
global_goal = kill_stalker
→ target exists
→ go to target
→ attack
```

Правильно:

```text
kill_stalker = operation
```

Операция должна включать:

```text
1. понять, кто цель;
2. понять, где цель может быть;
3. оценить уверенность информации;
4. оценить собственную боеготовность;
5. оценить силу цели;
6. решить: атаковать, готовиться, выслеживать, устроить засаду или отступить;
7. построить многошаговый план;
8. реагировать на события по пути;
9. чинить план, если цель ушла;
10. подтвердить смерть цели;
11. восстановиться/уйти после боя.
```

Итоговая формула:

```text
kill_stalker is not an action.
kill_stalker is an operation.
```

---

# 2. Как это работает сейчас

Текущая система уже имеет базовую заготовку:

```text
global_goal = kill_stalker
kill_target_id = <target agent id>
NeedScores.hunt_target
Intent(kind = hunt_target)
_plan_hunt_target / compatibility hunt logic
combat when target is co-located
goal completion when target is dead
```

Текущий уровень можно описать так:

```text
есть цель
→ появляется drive охоты
→ выбирается intent hunt_target
→ если цель рядом, начинается бой
→ если цель мертва, global_goal_achieved = true
```

Это полезный MVP, но он не моделирует:

```text
- поиск цели;
- устаревание информации;
- подготовку;
- оценку риска;
- покупку патронов;
- слежку;
- засаду;
- отход;
- восстановление после боя;
- социальные последствия.
```

---

# 3. Целевая модель после PR 5

После PR 5 `kill_stalker` должен раскладываться в цепочку objectives и ActivePlan:

```text
GlobalGoal: kill_stalker
  ↓
HuntOperation
  ↓
Objectives:
  - LOCATE_TARGET
  - TRACK_TARGET
  - PREPARE_FOR_HUNT
  - INTERCEPT_TARGET
  - AMBUSH_TARGET
  - ENGAGE_TARGET
  - CONFIRM_KILL
  - RETREAT_FROM_TARGET
  - RECOVER_AFTER_COMBAT
  ↓
ActivePlan:
  - PlanSteps
  - pause/resume
  - repair
  - abort/replace
```

---

# 4. Новая системная сущность: `TargetBelief`

## 4.1. Зачем нужна

Сейчас `kill_target_id` говорит только:

```text
кого убить
```

Но для системного поведения нужно знать:

```text
где цель вероятно находится
насколько свежая информация
насколько NPC уверен
что известно о вооружении цели
насколько цель опасна
есть ли у цели союзники
какие маршруты цель обычно использует
```

## 4.2. Модель

Рекомендуемый файл после PR 5:

```text
backend/app/games/zone_stalkers/decision/hunt/target_belief.py
```

Модель:

```python
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TargetLocationHypothesis:
    location_id: str
    confidence: float              # 0..1
    source: str                    # direct_seen | memory | intel | route_prediction
    last_seen_turn: int | None = None
    distance_estimate: int | None = None
    reason: str = ""


@dataclass(frozen=True)
class TargetBelief:
    target_id: str
    is_alive: bool | None
    alive_confidence: float

    last_known_location_id: str | None
    last_seen_turn: int | None
    location_confidence: float

    location_hypotheses: tuple[TargetLocationHypothesis, ...] = ()

    known_weapon_type: str | None = None
    known_armor_type: str | None = None
    known_group_ids: tuple[str, ...] = ()
    known_allies_nearby: tuple[str, ...] = ()

    estimated_hp: int | None = None
    estimated_combat_power: float | None = None

    source_memory_ids: tuple[str, ...] = ()
    uncertainty_reasons: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
```

## 4.3. Источники данных

`TargetBelief` должен собираться из:

```text
1. текущей видимости:
   - цель в той же локации;
   - цель в visible_entities;
   - цель участвует в combat_interactions;

2. MemoryStore:
   - target_seen;
   - target_last_known_location;
   - target_not_found;
   - target_route_observed;
   - target_equipment_seen;
   - target_combat_strength_observed;
   - target_death_confirmed;

3. intel:
   - trader gave target location;
   - rumor from other stalker;
   - group member report;

4. world facts:
   - target.is_alive if доступно через state;
   - target.has_left_zone;
   - target current known location if visible/allowed.
```

## 4.4. Устаревание уверенности

Пример простого правила:

```text
base_confidence = source_confidence
age_penalty = min(0.8, turns_since_seen / 300)
location_confidence = max(0.0, base_confidence - age_penalty)
```

Пример:

```text
видел цель 5 ходов назад:
  confidence ≈ 0.95

видел цель 100 ходов назад:
  confidence ≈ 0.60

видел цель 500 ходов назад:
  confidence ≈ 0.15
```

## 4.5. Негативные наблюдения

Если NPC пришёл в локацию и цели нет:

```text
MemoryRecord:
  kind = target_not_found
  location_id = ...
  target_id = ...
```

Это должно снижать confidence этой локации:

```text
target_seen at Bar, confidence 0.7
target_not_found at Bar after arrival
→ confidence Bar drops to 0.1
→ search next hypothesis
```

---

# 5. Новая системная сущность: `CombatReadiness`

## 5.1. Зачем нужна

NPC не должен атаковать только потому, что цель найдена.

Он должен оценить:

```text
готов ли я к бою?
достаточно ли у меня патронов?
есть ли оружие?
есть ли броня?
здоров ли я?
сильнее ли цель?
есть ли шанс выжить?
```

## 5.2. Модель

Рекомендуемый файл:

```text
backend/app/games/zone_stalkers/decision/hunt/combat_readiness.py
```

Модель:

```python
@dataclass(frozen=True)
class CombatReadiness:
    ready: bool
    readiness_score: float          # 0..1

    self_power: float
    target_power: float | None
    power_ratio: float | None       # self_power / target_power

    has_weapon: bool
    compatible_ammo_count: int
    has_armor: bool
    medicine_count: int
    hp_score: float

    blockers: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()
    confidence: float = 1.0
```

## 5.3. Пример расчёта `self_power`

```text
self_power =
  weapon_power        * 0.35
+ ammo_readiness      * 0.20
+ armor_power         * 0.15
+ hp_score            * 0.15
+ medicine_score      * 0.10
+ skill_score         * 0.05
```

Пример:

```text
weapon_power:
  no weapon = 0
  pistol = 0.4
  shotgun = 0.55
  ak74 = 0.75
  sniper = 0.9

ammo_readiness:
  0 ammo = 0
  1-2 ammo = 0.25
  3-5 ammo = 0.55
  6+ ammo = 1.0

armor_power:
  no armor = 0
  leather jacket = 0.3
  medium armor = 0.6
  exoskeleton = 0.9

hp_score:
  hp / 100
```

## 5.4. Пример расчёта `target_power`

Если цель видима:

```text
target_power = actual observed weapon/armor/hp
```

Если цель не видима:

```text
target_power = estimate from memory
```

Если информации нет:

```text
target_power = unknown
risk penalty increases
```

## 5.5. Recommendations

Если readiness недостаточен:

```text
blockers:
  - no_weapon
  - low_ammo
  - no_medicine
  - hp_low
  - target_too_strong
  - target_location_unknown
```

Recommendations:

```text
- RESUPPLY_WEAPON
- RESUPPLY_AMMO
- BUY_MEDICINE
- HEAL_SELF
- LOCATE_TARGET
- AMBUSH_TARGET
- RETREAT_FROM_TARGET
```

---

# 6. Hunt-specific objectives

## 6.1. Required objectives

После PR 5 добавить отдельные hunt objectives:

```text
LOCATE_TARGET
PREPARE_FOR_HUNT
TRACK_TARGET
INTERCEPT_TARGET
AMBUSH_TARGET
ENGAGE_TARGET
CONFIRM_KILL
RETREAT_FROM_TARGET
RECOVER_AFTER_COMBAT
```

## 6.2. `LOCATE_TARGET`

Используется, если:

```text
target location unknown
location confidence too low
last known location stale
target not found at expected location
```

Possible steps:

```text
1. retrieve last target memories
2. travel_to_last_known_location
3. observe_location_for_target
4. ask_for_intel
5. search_adjacent_locations
```

## 6.3. `PREPARE_FOR_HUNT`

Используется, если цель известна, но NPC не готов.

Triggers:

```text
no weapon
low ammo
low hp
no medicine
target stronger than NPC
```

Possible steps:

```text
1. resupply ammo
2. buy medicine
3. upgrade weapon/armor
4. heal
5. then resume hunt
```

## 6.4. `TRACK_TARGET`

Используется, если цель двигалась и есть route hypotheses.

Steps:

```text
1. travel_to probable route node
2. observe
3. update target belief
4. repair plan if target not found
```

## 6.5. `INTERCEPT_TARGET`

Используется, если есть предсказанный маршрут.

Steps:

```text
1. choose intercept location
2. travel there
3. wait/observe
4. engage if target appears
```

## 6.6. `AMBUSH_TARGET`

Используется, если:

```text
target is stronger
NPC has terrain advantage
target route is predictable
risk_tolerance allows ambush
```

Steps:

```text
1. choose ambush location
2. travel to ambush location
3. wait for target
4. engage with advantage
```

## 6.7. `ENGAGE_TARGET`

Используется только если:

```text
target is visible/co-located
combat readiness is acceptable
no blocking survival/emission objective
```

Steps:

```text
1. initiate_combat
2. combat loop
3. confirm outcome
```

## 6.8. `CONFIRM_KILL`

Используется после combat result или сообщения о смерти цели.

Completion conditions:

```text
target.is_alive == false
or target_death_confirmed memory with high confidence
```

Steps:

```text
1. observe target corpse / death event
2. write target_death_confirmed
3. mark global_goal_achieved
```

## 6.9. `RETREAT_FROM_TARGET`

Используется, если:

```text
hp critical
target too strong
ammo depleted
allies of target arrived
emission warning
```

Steps:

```text
1. flee to safe location
2. heal
3. decide whether to resume hunt or abort
```

## 6.10. `RECOVER_AFTER_COMBAT`

После успешного или неуспешного боя:

```text
1. heal if needed
2. reload/resupply ammo if low
3. loot if safe
4. leave dangerous area
```

---

# 7. Hunt operation lifecycle

## 7.1. State machine

```text
UNKNOWN_TARGET
  → LOCATING
  → PREPARING
  → TRACKING
  → INTERCEPTING
  → ENGAGING
  → CONFIRMING
  → COMPLETED

Temporary states:
  PAUSED_FOR_SURVIVAL
  PAUSED_FOR_EMISSION
  RETREATING
  REPAIRING_ROUTE
  ABORTED
```

## 7.2. Typical flow

```text
1. target id known
2. target location unknown
3. LOCATE_TARGET
4. target likely at Bar
5. PREPARE_FOR_HUNT if ammo low
6. TRACK_TARGET / travel to Bar
7. target not found
8. repair to Checkpoint
9. target found
10. ENGAGE_TARGET
11. target killed
12. CONFIRM_KILL
13. RECOVER_AFTER_COMBAT
```

---

# 8. Plan repair rules

## 8.1. Target not found

Condition:

```text
current step = observe_location_for_target
result = target not found
```

Repair:

```text
1. write target_not_found memory
2. reduce confidence for current location
3. query next location hypothesis
4. replace current target location
5. continue same objective if confidence remains acceptable
```

If no hypotheses remain:

```text
replace objective:
  HUNT_TARGET → LOCATE_TARGET / ASK_FOR_INTEL
```

## 8.2. Target moved

Condition:

```text
new target_seen memory appears at different location
```

Repair:

```text
1. cancel current travel target
2. retarget to new location
3. keep objective HUNT_TARGET
```

## 8.3. NPC not ready anymore

Condition:

```text
ammo depleted
hp low
weapon broken
medicine depleted
```

Repair or pause:

```text
if survival critical:
  pause HUNT_TARGET
  HEAL_SELF / RESTORE_WATER / RESTORE_FOOD

if combat readiness below threshold:
  replace objective with PREPARE_FOR_HUNT
```

## 8.4. Emission warning

Condition:

```text
emission_imminent or emission_active
```

Decision:

```text
pause HUNT_TARGET
run REACH_SAFE_SHELTER
after emission_ended:
  resume/repair HUNT_TARGET
```

## 8.5. Target already dead

Condition:

```text
target.is_alive == false
```

Decision:

```text
CONFIRM_KILL
complete global_goal
```

If death source uncertain:

```text
create target_death_reported memory
confidence < 1.0
maybe verify if needed
```

---

# 9. Information economy for hunting

## 9.1. Why needed

If NPC does not know where target is, it needs systemic ways to acquire information.

Currently the code already has a concept of `STEP_ASK_FOR_INTEL` and `_HUNT_INTEL_PRICE`.

Post-PR5, this should become a real mechanic.

## 9.2. Intel sources

```text
trader:
  sells last known target location

other stalker:
  may share rumor

group member:
  reports seen target

dead body / combat memory:
  tells where target was last active

environment:
  target traces / recently visited location
```

## 9.3. Intel quality

Intel should have:

```text
source
confidence
age
cost
location_id
target_id
```

Example:

```python
MemoryRecord(
    kind="target_intel",
    summary="Торговец сказал, что цель видели у Бара.",
    location_id="loc_bar",
    entity_ids=("target_1", "trader_1"),
    confidence=0.75,
    tags=("target", "intel", "location"),
)
```

## 9.4. Objective scoring

If no strong location hypothesis:

```text
ASK_FOR_INTEL / LOCATE_TARGET should beat direct HUNT_TARGET.
```

---

# 10. Combat engagement policy

## 10.1. Direct attack

Allowed if:

```text
target visible
self readiness acceptable
power_ratio >= direct_attack_threshold
no critical survival need
no emission warning
```

Suggested threshold:

```text
power_ratio >= 0.9 for high risk_tolerance
power_ratio >= 1.1 for normal
power_ratio >= 1.3 for low risk_tolerance
```

## 10.2. Ambush

Prefer ambush if:

```text
target stronger
route predictable
terrain has ambush advantage
NPC risk_tolerance moderate/high
```

## 10.3. Retreat

Retreat if:

```text
hp low
ammo depleted
target much stronger
target has allies
emission threat
```

## 10.4. Wait and observe

Use if:

```text
target likely nearby
confidence moderate
combat readiness not perfect
terrain safe
```

---

# 11. Social consequences

This is optional but important for a living simulation.

A murder should not be purely private.

Possible mechanics:

```text
witnesses
faction reputation
bounty
revenge goals
relations changes
group hostility
trader trust changes
```

## 11.1. Witness system

When combat/kill occurs:

```text
nearby NPCs may witness
witness memory: witnessed_murder
```

Effects:

```text
target faction becomes hostile
friends of target may get revenge objective
trader may refuse service
```

## 11.2. Revenge chain

If NPC A kills NPC B:

```text
B's ally C may receive:
  global_goal = avenge_ally
  kill_target_id = A
```

This can reuse hunt operation mechanics.

## 11.3. Reputation

Memory/reputation effects:

```text
killer reputation:
  dangerous
  murderer
  bandit-like
  unreliable
```

This can later feed into:

```text
negotiate
join_group
trade prices
help_ally
```

---

# 12. BrainTrace requirements

Hunt logic must be explainable.

Example trace:

```json
{
  "current_thought": "Хочу убить Поцик 2, но у меня мало патронов. Сначала подготовлюсь.",
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
      "key": "LOCATE_TARGET",
      "score": 0.42,
      "decision": "rejected",
      "reason": "Местоположение цели известно с приемлемой уверенностью"
    }
  ],
  "target_belief": {
    "target_id": "agent_target_1",
    "last_known_location_id": "loc_bar",
    "location_confidence": 0.7,
    "last_seen_turn": 1234
  },
  "combat_readiness": {
    "ready": false,
    "readiness_score": 0.48,
    "blockers": ["low_ammo"],
    "recommendations": ["RESUPPLY_AMMO"]
  },
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
  ]
}
```

Do not dump huge structures.

Limits:

```text
target_belief.location_hypotheses <= 3
objective_scores <= 5
memory_used <= 5
active_plan.steps shown <= 5
```

---

# 13. Frontend/debug requirements

Agent profile should show:

```text
Цель охоты:
  Убить: Поцик 2

Где цель:
  Бар, уверенность 70%, видел 30 ходов назад

Готовность:
  Не готов — мало патронов

Текущая подцель:
  Подготовиться к охоте

План:
  1. Дойти до торговца
  2. Купить патроны
  3. Идти к Бару
  4. Проверить цель
  5. Атаковать
```

If target not found:

```text
План отремонтирован:
  Цели нет у Бара.
  Следующая вероятная локация: КПП.
```

---

# 14. Suggested implementation stages after PR 5

## Stage H1 — TargetBelief MVP

Files:

```text
backend/app/games/zone_stalkers/decision/hunt/target_belief.py
backend/tests/decision/v3/test_target_belief.py
```

Features:

```text
- build TargetBelief from current visibility and memory;
- last known location;
- confidence decay;
- target_not_found reduces location confidence;
- brain_trace target_belief block.
```

## Stage H2 — CombatReadiness MVP

Files:

```text
backend/app/games/zone_stalkers/decision/hunt/combat_readiness.py
backend/tests/decision/v3/test_combat_readiness.py
```

Features:

```text
- self_power;
- target_power estimate;
- readiness_score;
- blockers/recommendations;
- no direct attack if no weapon/ammo/hp low.
```

## Stage H3 — Hunt objectives

Add objectives:

```text
LOCATE_TARGET
PREPARE_FOR_HUNT
TRACK_TARGET
ENGAGE_TARGET
CONFIRM_KILL
RETREAT_FROM_TARGET
```

Features:

```text
- Objective generation from TargetBelief + CombatReadiness;
- scoring;
- rejected alternatives.
```

## Stage H4 — Hunt ActivePlan templates

Plan templates:

```text
prepare_for_hunt
locate_target
track_target
engage_target
confirm_kill
retreat
```

Features:

```text
- plan steps;
- pause/resume;
- repair if target not found.
```

## Stage H5 — Intel economy

Features:

```text
- ask trader for target intel;
- target_intel memory;
- intel confidence;
- intel price;
- use intel in TargetBelief.
```

## Stage H6 — Ambush/intercept

Features:

```text
- route prediction;
- terrain advantage;
- ambush objective;
- wait for target;
- engage with advantage.
```

## Stage H7 — Social consequences

Features:

```text
- witnesses;
- faction/reputation impact;
- revenge goal;
- bounty.
```

---

# 15. Suggested tests

## 15.1. Does not attack without ammo

```python
def test_hunt_target_prepares_when_ammo_low():
    ...
```

Expected:

```text
selected objective = PREPARE_FOR_HUNT / RESUPPLY_AMMO
HUNT_TARGET rejected because low_ammo
no combat initiated
```

## 15.2. Locate target from memory

```python
def test_locate_target_from_last_seen_memory():
    ...
```

Expected:

```text
TargetBelief.last_known_location_id = loc_bar
location_confidence > 0
plan travels to loc_bar
```

## 15.3. Repair when target not found

```python
def test_hunt_plan_repairs_when_target_not_found():
    ...
```

Expected:

```text
target_not_found memory written
old location confidence decreases
next hypothesis selected
plan repaired
```

## 15.4. Pause hunt for emission

```python
def test_hunt_plan_pauses_for_emission_and_resumes():
    ...
```

Expected:

```text
HUNT_TARGET paused
REACH_SAFE_SHELTER active
after emission_ended HUNT_TARGET resumes/repairs
```

## 15.5. Confirm kill

```python
def test_hunt_target_confirms_kill_and_completes_goal():
    ...
```

Expected:

```text
target dead
CONFIRM_KILL completed
global_goal_achieved = true
target_death_confirmed memory
```

## 15.6. Do not trust stale target memory too much

```python
def test_stale_target_location_lowers_confidence_and_prefers_locate():
    ...
```

Expected:

```text
old last_seen memory confidence low
LOCATE_TARGET / ASK_FOR_INTEL selected
not direct travel/attack
```

---

# 16. Definition of Done for systemic hunt

The post-PR5 hunt system is acceptable when:

```text
[ ] TargetBelief exists and is shown in brain_trace.
[ ] CombatReadiness exists and blocks suicidal attacks.
[ ] Hunt objectives are generated separately from generic HUNT_TARGET.
[ ] NPC can prepare before hunting.
[ ] NPC can locate target from memory/intel.
[ ] NPC repairs plan when target not found.
[ ] NPC pauses hunt for survival/emission.
[ ] NPC resumes hunt after temporary interruption.
[ ] NPC confirms kill before completing global goal.
[ ] NPC writes meaningful hunt-related memory.
[ ] Frontend/debug explains why NPC attacks, prepares, tracks, retreats or gives up.
```

---

# 17. Final design principle

`kill_stalker` should be implemented through the same system as everything else:

```text
memory
belief
needs
objectives
active plan
repair
pause/resume
trace
```

Do not implement it as a separate exception-heavy subsystem.

Correct mental model:

```text
A stalker does not "execute kill_stalker".
A stalker conducts a hunt operation.
```
