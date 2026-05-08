# NPC Brain v3 — PR 2 Revised Implementation Contract

> Проект: `zone_stalkers`  
> Предыдущий этап: PR 1 — `PlanMonitor`, `brain_trace`, финализация сна, survival-preconditions для отдыха.  
> Цель PR 2: сделать потребности НПЦ в предметах, деньгах и survival-покупках системными, объяснимыми и пригодными для дальнейшего перехода к `Objective`/`BeliefState`.

---

## 1. Почему PR 2 нужно доработать после финального PR 1

Изначально PR 2 был про `ItemNeed`:

```text
food
drink
medicine
weapon
armor
ammo
upgrade
```

Но после анализа дампа `Поцик 1` стало видно, что одной модели запасов недостаточно.

Есть две разные проблемы:

```text
ImmediateNeed:
  НПЦ прямо сейчас голоден / хочет пить / ранен и должен потребить то, что уже есть.

StockNeed / ItemNeed:
  НПЦ хочет иметь запас еды/воды/патронов/медицины/оружие/броню на будущее.
```

Если оставить только `ItemNeed`, то система продолжит смешивать:

```text
"у меня нет еды в запасе"
и
"я голоден 86%, надо съесть хлеб из инвентаря"
```

Поэтому PR 2 должен быть шире прежнего документа:

```text
PR 2 = ImmediateNeed + ItemNeed + Affordability/Liquidity + survival-mode purchasing.
```

Но PR 2 всё ещё не должен становиться полным `ObjectiveGenerator`.

---

## 2. Цель PR 2

PR 2 должен ответить на вопросы:

1. Что НПЦ должен **потребить прямо сейчас**?
2. Каких предметов НПЦ **не хватает в запасе**?
3. Что НПЦ может **позволить себе купить**?
4. Если денег не хватает, что он может **безопасно продать**?
5. Почему planner выбрал именно этот предмет / покупку / продажу?
6. Как это отобразить в `brain_trace`?

---

## 3. Non-goals for PR 2

PR 2 НЕ делает:

- полный `NPCBrain.tick`;
- полноценный `BeliefState`;
- полноценный `ObjectiveGenerator`;
- Redis;
- новую storage-систему памяти;
- перенос `scheduled_action` на `ActivePlan` source of truth;
- `pause/adapt` lifecycle;
- GOAP/HTN planner;
- социальную экономику;
- полноценную симуляцию рынка;
- умное групповое снабжение;
- полную замену `NeedScores`.

PR 2 должен быть совместимым слоем поверх текущей v2 decision pipeline.

---

## 4. Новые сущности PR 2

### 4.1. `ImmediateNeed`

`ImmediateNeed` описывает не запас, а срочное действие по восстановлению состояния.

Рекомендуемый файл:

```text
backend/app/games/zone_stalkers/decision/models/immediate_need.py
```

Модель:

```python
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ImmediateNeed:
    key: str                         # drink_now, eat_now, heal_now
    urgency: float                   # 0..1
    current_value: float             # thirst/hunger/hp
    threshold: float
    trigger_context: str = "survival"  # survival | rest_preparation | healing
    blocks_intents: frozenset[str] = field(default_factory=frozenset)
    available_inventory_item_types: frozenset[str] = field(default_factory=frozenset)
    selected_item_id: str | None = None
    selected_item_type: str | None = None
    reason: str = ""
    source_factors: tuple[dict[str, Any], ...] = ()
```

Допустимые `key` в PR 2:

```text
drink_now
eat_now
heal_now
```

### 4.2. `ItemNeed`

`ItemNeed` описывает запас/снаряжение.

Рекомендуемый файл:

```text
backend/app/games/zone_stalkers/decision/models/item_need.py
```

Модель:

```python
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ItemNeed:
    key: str
    desired_count: int
    current_count: int
    missing_count: int
    urgency: float
    compatible_item_types: frozenset[str] = field(default_factory=frozenset)
    reason: str = ""
    priority: int = 100
    source_factors: tuple[dict[str, Any], ...] = ()
    expected_min_price: int | None = None
    affordability_hint: str | None = None  # affordable | unaffordable | unknown
```

Допустимые `key`:

```text
food
drink
medicine
weapon
armor
ammo
upgrade
```

### 4.3. `AffordabilityResult`

Чтобы planner не пытался купить невозможное, вводим явный результат проверки цены.

Рекомендуемый файл:

```text
backend/app/games/zone_stalkers/decision/models/affordability.py
```

Модель:

```python
@dataclass(frozen=True)
class AffordabilityResult:
    can_buy_now: bool
    required_price: int | None
    current_money: int
    money_missing: int
    cheapest_viable_item_type: str | None
    cheapest_viable_item_name: str | None
    reason: str
```

### 4.4. `LiquidityOption`

Если денег не хватает, planner должен знать, что можно продать.

```python
@dataclass(frozen=True)
class LiquidityOption:
    item_id: str
    item_type: str
    item_name: str
    estimated_sell_value: int
    safety: str                 # safe, risky, emergency_only, forbidden
    reason: str
```

### 4.5. `NeedEvaluationResult`

Чтобы не пересчитывать и не рассинхронизировать `ImmediateNeed`/`ItemNeed` между `needs`, planner и `brain_trace`, вводим общий контейнер:

```python
@dataclass(frozen=True)
class NeedEvaluationResult:
    scores: NeedScores
    immediate_needs: tuple[ImmediateNeed, ...]
    item_needs: tuple[ItemNeed, ...]
    liquidity_summary: dict | None = None
```

Инвариант:

```text
planner и brain_trace должны использовать один и тот же NeedEvaluationResult,
а не пересчитывать item/immediate needs локально повторно.
```

---

## 5. Где считать новые сущности

### 5.1. Immediate needs

Новый файл:

```text
backend/app/games/zone_stalkers/decision/immediate_needs.py
```

Функции:

```python
def evaluate_immediate_needs(ctx: AgentContext, state: dict) -> list[ImmediateNeed]:
    ...
```

Helpers:

```python
_find_best_inventory_food(...)
_find_best_inventory_drink(...)
_find_best_inventory_heal(...)
```

### 5.2. Item needs

Новый файл:

```text
backend/app/games/zone_stalkers/decision/item_needs.py
```

Функции:

```python
def evaluate_item_needs(ctx: AgentContext, state: dict) -> list[ItemNeed]:
    ...
```

### 5.3. Affordability / liquidity

Новый файл:

```text
backend/app/games/zone_stalkers/decision/liquidity.py
```

Функции:

```python
def find_cheapest_viable_trader_item(
    *,
    trader: dict,
    category: str,
    compatible_item_types: set[str] | None,
) -> dict | None:
    ...

def evaluate_affordability(
    *,
    agent: dict,
    trader: dict,
    category: str,
    compatible_item_types: set[str] | None = None,
) -> AffordabilityResult:
    ...

def find_liquidity_options(
    *,
    agent: dict,
    immediate_needs: list[ImmediateNeed],
    item_needs: list[ItemNeed],
) -> list[LiquidityOption]:
    ...
```

---

## 6. ImmediateNeed rules

### 6.0. Context rules

`trigger_context` определяет область действия `ImmediateNeed`:

```text
trigger_context = "survival":
  глобально блокирующая срочная нужда.

trigger_context = "rest_preparation":
  влияет только на планирование сна/отдыха.

trigger_context = "healing":
  срочная потребность лечения.
```

Ключевой инвариант:

```text
critical immediate needs affect intent selection;
rest-preparation immediate needs affect only rest planning.
```

### 6.1. Drink now

```text
if thirst >= CRITICAL_THIRST_THRESHOLD:
    urgency = thirst / 100
    if drink item in inventory:
        selected item = best available drink
```

Если `thirst` ниже critical, но выше safe sleep threshold, эта need создаётся как rest-preparation:

```text
if thirst >= SLEEP_SAFE_THIRST_THRESHOLD:
    urgency = 0.70–0.79
    trigger_context = "rest_preparation"
```

### 6.2. Eat now

```text
if hunger >= CRITICAL_HUNGER_THRESHOLD:
    urgency = hunger / 100
    if food item in inventory:
        selected item = best available food
```

### 6.3. Heal now

```text
if hp <= healing threshold and heal item exists:
    urgency = heal_self score
```

### 6.4. Выбор предмета из инвентаря

В immediate survival mode выбирать не самый дорогой предмет, а минимально достаточный.

Пример:

```text
hunger=86, inventory=[bread, glucose]
eat_now should prefer bread unless glucose is explicitly better for emergency.
```

Простое правило PR 2:

```text
для еды/воды:
  выбрать самый дешёвый подходящий item из inventory
```

Это может казаться странным, но для текущей экономики это рационально: дорогие предметы лучше оставить для продажи/экстренных случаев.

---

## 7. ItemNeed rules

### 7.0. Dominant ItemNeed selection

`ItemNeed` должен выбираться по score (`urgency`), а не по фиксированной очереди категорий.

```python
candidate_item_needs = [n for n in item_needs if n.urgency > 0 and n.key != "upgrade"]
candidate_item_needs.sort(key=lambda n: (-n.urgency, n.priority, n.key))
dominant = candidate_item_needs[0] if candidate_item_needs else None
```

Инвариант:

```text
ImmediateNeed first.
Then ItemNeed by urgency score.
Priority is deterministic tie-breaker only.
```

### 7.1. Food stock

```text
desired_food = depends on risk_tolerance
current_food = count food items in inventory
missing = max(0, desired_food - current_food)
urgency = 0.55 if missing > 0 else 0
```

### 7.2. Drink stock

```text
desired_drink = depends on risk_tolerance
current_drink = count drink items
missing = max(0, desired_drink - current_drink)
urgency = 0.55 if missing > 0 else 0
```

### 7.3. Medicine stock

```text
desired_medicine = depends on risk_tolerance
current_medicine = count heal items
missing = max(0, desired_medicine - current_medicine)
urgency = 0.45 if missing > 0 else 0
```

### 7.4. Weapon

```text
if equipment.weapon is None:
    urgency = 0.65
```

### 7.5. Armor

```text
if equipment.armor is None:
    urgency = 0.70
```

### 7.6. Ammo

```text
if weapon exists:
    desired = DESIRED_AMMO_COUNT
    current = compatible ammo count
    urgency = 0.60 * missing / desired
else:
    urgency = 0
```

### 7.7. Upgrade

`upgrade` можно считать debug-only в PR 2.

По умолчанию:

```python
reload_or_rearm = max(n.urgency for n in item_needs if n.key != "upgrade")
```

---

## 8. Как меняется `NeedScores`

`NeedScores` остаётся.

Но `evaluate_needs()` должен использовать новые структуры:

```python
immediate_needs = evaluate_immediate_needs(ctx, state)
item_needs = evaluate_item_needs(ctx, state)

needs.drink = max(existing_drink_score, drink_now.urgency)
needs.eat = max(existing_eat_score, eat_now.urgency)
needs.heal_self = max(existing_heal_score, heal_now.urgency)
needs.reload_or_rearm = max(item_need.urgency for item_need in item_needs if item_need.key != "upgrade")
```

Важно:

```text
ImmediateNeed не должен попадать в reload_or_rearm.
ItemNeed не должен заменять eat/drink/heal.
```

---

## 9. Как меняется `select_intent`

После финального PR 1 `select_intent()` уже должен использовать общие thresholds из `tick_constants.py`.

PR 2 должен закрепить:

```text
critical drink/eat/heal всегда имеют приоритет над sleep/resupply/get_rich.
```

Это важно, чтобы NPC не покупал оружие, когда у него в инвентаре есть вода, а жажда критическая.

---

## 10. Как меняется planner

### 10.1. Общий порядок

Planner должен сначала обслуживать immediate needs, потом stock/equipment needs.

```text
ImmediateNeed:
  drink_now
  eat_now
  heal_now

ItemNeed:
  dominant item need by urgency score (priority only tie-breaker)
```

### 10.2. `seek_water`

Если есть `ImmediateNeed.drink_now` и выбран item в inventory:

```text
plan = consume_item(selected_item_id)
```

Если в inventory нет:

```text
1. find affordable water at known/current trader
2. if cannot afford:
     sell safe liquidity item
3. if cannot buy:
     travel/search remembered water
```

### 10.3. `seek_food`

Аналогично.

### 10.4. `heal_self`

Аналогично, но sale policy осторожнее: не продавать последний medkit, если hp низкий.

### 10.5. `resupply`

`_plan_resupply()` должен использовать `ItemNeed`, а не пересчитывать gaps.

```python
dominant = choose_dominant_item_need(item_needs)
```

`choose_dominant_item_need(...)` должен использовать score-first семантику из раздела 7.0.

Если dominant — `weapon`, но есть критический `ImmediateNeed`, значит до resupply planner вообще не должен дойти.

### 10.6. `rest`

После финального PR 1 `_plan_rest()` уже добавляет preparation steps перед sleep. PR 2 должен использовать `ImmediateNeed` вместо локальной логики:

```text
if drink_now exists and inventory item available:
    add consume drink step before sleep

if eat_now exists and inventory item available:
    add consume food step before sleep
```

---

## 11. Survival-mode purchasing

### 11.1. Проблема

NPC покупал дорогие survival-предметы:

```text
glucose за 180
energy_drink за 120
```

Когда он беден и needs critical, нужно покупать не лучший по качеству, а лучший по выживанию за деньги.

### 11.2. Правило PR 2

Если покупка вызвана immediate critical need:

```text
choose cheapest affordable viable item
```

Viable = item category matches need.

Примеры:

```text
critical hunger:
  bread за 30 лучше glucose за 180

critical thirst:
  water за 45 лучше energy_drink за 120
```

### 11.3. Если денег хватает на несколько вариантов

Для survival mode сортировка:

```python
(price, -need_resolution_score, item_type)
```

Для normal mode можно оставить старый risk/value/weight scoring.

### 11.4. Distinguish reasons

```text
buy_food_stock
buy_drink_stock
buy_food_survival
buy_drink_survival
buy_weapon_resupply
buy_armor_resupply
buy_ammo_resupply
buy_medical_resupply
```

---

## 12. LiquidityPlan

### 12.1. Проблема

Сейчас логика местами проверяет:

```python
money == 0
```

А нужна проверка:

```python
money < required_price
```

### 12.2. Правило

Если NPC хочет купить survival/item need, но денег не хватает:

```text
1. проверить safe liquidity options;
2. если есть safe option → sell → buy;
3. если нет safe option, но need critical → emergency_only sale allowed;
4. если ничего нельзя продать → fallback to search/loot/get_rich.
```

### 12.4. Запрет unaffordable buy loop

Planner не должен повторять один и тот же неисполняемый buy-план без изменения условий:

```text
trade_buy_item weapon
trade_buy_item weapon
trade_buy_item weapon
...
```

При `money < cheapest_viable_item_price` следующий шаг должен быть ликвидностью/фолбэком:

```text
sell safe item
fallback_get_money
fallback_search_item
fallback_wait_no_action (только если нет actionable альтернатив)
```

### 12.3. Что можно продавать

Safe:

```text
артефакты
дубликаты предметов
патроны неподходящего калибра
лишние consumables сверх desired count
```

Risky:

```text
дорогие consumables, если есть дешёвые заменители
часть медицины, если hp высокий
```

Emergency only:

```text
последняя аптечка при hp нормальном, если hunger/thirst смертельны
```

Forbidden:

```text
экипированное оружие
экипированная броня
последняя вода при thirst high
последняя еда при hunger high
последний heal item при hp low
```

---

## 13. BrainTrace additions

PR 2 должен добавить optional поля:

```json
{
  "immediate_needs": [
    {
      "key": "drink_now",
      "urgency": 0.8,
      "selected_item_type": "water",
      "reason": "Жажда 80%, вода есть в инвентаре"
    }
  ],
  "item_needs": [
    {
      "key": "weapon",
      "urgency": 0.65,
      "missing_count": 1,
      "reason": "Нет оружия"
    }
  ],
  "liquidity": {
    "can_buy_now": false,
    "money_missing": 40,
    "safe_sale_options": 1
  }
}
```

Limit:

```text
immediate_needs <= 3
item_needs <= 5
liquidity options summary only, not full inventory dump
```

---

## 14. Frontend scope

В `AgentProfileModal` добавить компактные блоки:

```text
Срочные нужды:
  - Выпить воду: жажда 80%, вода есть
  - Поесть хлеб: голод 86%, хлеб есть

Запасы / снаряжение:
  - Нет оружия
  - Патронов 0/3

Деньги:
  - Не хватает 40 на воду
  - Можно продать: артефакт / лишний бинт
```

Не делать сложные таблицы и графики.

---

## 15. Expected files

### New backend files

```text
backend/app/games/zone_stalkers/decision/models/immediate_need.py
backend/app/games/zone_stalkers/decision/models/item_need.py
backend/app/games/zone_stalkers/decision/models/affordability.py
backend/app/games/zone_stalkers/decision/immediate_needs.py
backend/app/games/zone_stalkers/decision/item_needs.py
backend/app/games/zone_stalkers/decision/liquidity.py
```

### Changed backend files

```text
backend/app/games/zone_stalkers/decision/needs.py
backend/app/games/zone_stalkers/decision/intents.py
backend/app/games/zone_stalkers/decision/planner.py
backend/app/games/zone_stalkers/decision/executors.py
backend/app/games/zone_stalkers/decision/debug/brain_trace.py
backend/app/games/zone_stalkers/rules/tick_constants.py
```

### Tests

```text
backend/tests/decision/v3/test_immediate_needs.py
backend/tests/decision/v3/test_item_needs.py
backend/tests/decision/v3/test_liquidity.py
backend/tests/decision/v3/test_survival_purchasing.py
backend/tests/decision/v3/test_brain_trace_needs.py
backend/tests/decision/test_planner.py
backend/tests/decision/test_needs.py
```

### Frontend

```text
frontend/src/games/zone_stalkers/ui/AgentProfileModal.tsx
```

---

## 16. Test plan

### Immediate needs

```text
thirst >= 80 + water in inventory → drink_now selected
hunger >= 80 + bread in inventory → eat_now selected
hp low + medkit in inventory → heal_now selected
```

### Critical needs beat sleep/resupply

```text
hunger 86 + sleepiness 98 → seek_food
thirst 80 + sleepiness 98 → seek_water
critical hunger + no weapon → seek_food, not resupply
```

### Item needs

```text
no weapon → weapon item_need urgency 0.65
armor exists → armor item_need urgency 0
weapon + missing ammo → ammo item_need
no food stock → food item_need
```

### Survival purchasing

```text
critical hunger + bread affordable + glucose affordable → buy bread
critical thirst + water affordable + energy drink affordable → buy water
```

### Liquidity

```text
money < cheapest food price + artifact in inventory → sell artifact then buy food
money < price + only last water at high thirst → do not sell last water
money > price → buy directly
no repeated unaffordable trade_buy_item under unchanged conditions
```

### Planner

```text
seek_food with inventory food → consume, not buy
seek_water with inventory water → consume, not buy
resupply uses ItemNeed dominant need
rest-preparation immediate needs affect only rest plan
critical immediate needs still beat sleep/resupply
sleep behavior from PR1 remains unchanged (dynamic duration + early wake)
```

### BrainTrace

```text
brain_trace.immediate_needs present
brain_trace.item_needs present
brain_trace does not dump full inventory
```

---

## 17. Definition of Done

PR 2 is done when:

- [ ] `ImmediateNeed` exists and is used by `seek_food`, `seek_water`, `heal_self`, `rest`.
- [ ] `ItemNeed` exists and drives `reload_or_rearm`.
- [ ] `resupply` planner uses `ItemNeed`.
- [ ] Critical hunger/thirst with inventory item creates consume plan, not sleep/resupply/buy.
- [ ] Survival-mode purchasing chooses cheapest affordable viable item.
- [ ] Planner checks `money < required_price`, not only `money == 0`.
- [ ] Liquidity options exist and forbid selling critical last survival items.
- [ ] `brain_trace` shows immediate needs, item needs and liquidity summary.
- [ ] Existing PR 1 guarantees remain true.
- [ ] Tests cover the `Поцик 1`-style case.

---

## 18. Canonical regression cases: `Поцик 1`

Given:

```text
hunger = 86
thirst = 80
sleepiness = 98
money = 59
weapon = null
armor = leather_jacket
inventory = bread, glucose, energy_drink, water, bandage, medkit
```

Expected behavior after PR 2 (critical survival case):

```text
1. drink_now selected because thirst=80 and water exists;
2. consume water;
3. eat_now selected because hunger=86 and bread/glucose exists;
4. consume bread before glucose unless glucose is explicitly emergency-only;
5. rest after survival needs fall below thresholds;
6. after rest, resupply weapon becomes relevant;
7. if buying weapon is impossible, planner does not loop on unaffordable buy;
8. brain_trace clearly explains this sequence.
```

### 18.1. Stabilized post-PR1 case

Given:

```text
hunger = 50
thirst = 57
sleepiness = 46
money = 29
weapon = null
armor = leather_jacket
inventory = bread, bandage, medkit
scheduled_action = null
current_goal = resupply
```

Expected behavior:

```text
1. No critical ImmediateNeed is active.
2. ItemNeed.weapon can dominate food stock by urgency score.
3. Planner tries to resolve weapon need.
4. If no affordable weapon exists:
   - planner does not loop on unaffordable buy;
   - planner switches to liquidity/fallback path.
5. Last bread is not sold when it violates survival reserve.
```

---

## 19. Follow-up after PR 2

PR 2 completes the need/economy layer.

Next step should be PR 3:

```text
MemoryStore v3 MVP + BeliefState adapter.
```

Reason:

```text
Once needs and liquidity are explicit, NPC needs better memory retrieval to choose where to buy/find/sell.
```
