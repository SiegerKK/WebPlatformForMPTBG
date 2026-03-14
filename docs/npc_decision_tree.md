# Дерево принятия решений НПЦ — Zone Stalkers

> Документ описывает **актуальное** поведение НПЦ-сталкера на основе кода в
> `backend/app/games/zone_stalkers/rules/tick_rules.py` и
> `backend/app/games/zone_stalkers/generators/zone_generator.py`.

---

## 1. Модель агента — единый класс

НПЦ и игрок — это **один и тот же объект** (`stalker_agent`), создаваемый функцией
`_make_stalker_agent()`.  Единственное отличие — поле `controller.kind`:

| Значение | Кто управляет |
|---|---|
| `"bot"` | Бот (AI-логика из `tick_rules.py`) |
| `"human"` | Живой игрок (команды через API) |

Игрок может взять под управление любого НПЦ командой `take_control`. После этого
агент переходит в режим `"human"`, а бот-логика для него больше не вызывается.
Если игрок переключается на другого агента, бывший персонаж возвращается в `"bot"`.

### Поля агента

```
id, archetype="stalker_agent", name, location_id
hp / max_hp, radiation
hunger (0–100), thirst (0–100), sleepiness (0–100)  ← выше = хуже
money, inventory[], equipment{}
faction  ← "loner" | "military" | "duty" | "freedom"
controller { kind, participant_id }
is_alive, action_used
experience, skill_combat, skill_stalker, skill_trade, skill_medicine, skill_social
global_goal       ← "survive" | "get_rich" | "explore_zone" | "help_others" | "find_wish"
current_goal      ← строка, отражает текущий выбор в цикле тика
risk_tolerance    ← 0.2–0.9
material_threshold← 500–3000 RU  (порог накопления перед переходом к цели)
scheduled_action  ← активное длительное действие или null
action_queue[]    ← очередь следующих действий
memory[]          ← журнал последних 50 событий агента
```

## 1b. Модель торговца

Торговец (`trader_npc`) — скриптовый НПЦ, всегда остающийся на одной локации.
Управляется НЕ ботом; его покупки инициируются сталкером через механику продажи.

```
id, archetype="trader_npc", name, location_id
inventory[]   ← ассортимент (с полем stock)
money         ← наличность
memory[]      ← журнал последних 50 транзакций (аналогичная структура)
```

Создать отладочного торговца: команда `debug_spawn_trader(loc_id, name?)`.

---

## 2. Как устроен игровой тик

Один **тик = 1 игровая минута** (константа `MINUTES_PER_TURN = 1`).

`tick_zone_map(state)` вызывается при команде `end_turn` от игрока (или по таймеру).
Порядок шагов внутри тика:

```
1. Обработать scheduled_action каждого живого агента
2. Деградация потребностей (раз в 60 тиков = раз в час)
3. Принятие решений для каждого bot-агента без active scheduled_action
4. Продвинуть время: world_minute++, rollover → hour → day
5. Сбросить action_used = False для всех живых агентов
6. Проверить окончание игры (world_turn > max_turns)
```

---

## 3. Что происходит со спавном нового НПЦ

### Шаг 0 — Создание агента

При вызове `debug_spawn_stalker` (или через генератор `generate_zone`):

- `controller.kind = "bot"`, `participant_id = None`
- `global_goal` — случайный из вариантов (или задаётся явно в debug_spawn_stalker)
- `material_threshold` — случайное число 500–3000
- `risk_tolerance` — случайное 0.2–0.9
- `faction` — случайная
- Начальный инвентарь: бинт, возможно медкит, возможно еда; оружие/бронежилет — случайно
- `scheduled_action = None`, `memory = []`
- `hunger = 20`, `thirst = 20`, `sleepiness = 10`

### Шаги 1–N — Каждый тик

Пока у агента нет `scheduled_action`, бот-логика (`_run_bot_action`) вызывается в шаге 3
каждого тика и **выбирает ровно одно действие**.

---

## 4. Дерево принятия решений (`_run_bot_action`)

```
_run_bot_action(agent)
│
├─ ЭКСТРЕННЫЙ УРОВЕНЬ (всегда проверяется первым)
│   │
│   ├─ HP ≤ 30?
│   │   ├─ Есть предмет лечения (medkit/bandage)?
│   │   │   └─ ✅ Употребить предмет (item_consumed)
│   │   └─ Нет предмета лечения
│   │       ├─ Есть безопасный сосед (anomaly_activity ≤ 3)?
│   │       │   └─ ✅ travel → безопасная локация (1 хоп)
│   │       └─ Нет соседей → ждать (action_used = True)
│   │
│   ├─ Голод ≥ 70?
│   │   └─ Есть еда (bread/energy_drink)?
│   │       └─ ✅ Съесть
│   │         memory: (нет — стандартный item_consumed event)
│   │
│   └─ Жажда ≥ 70?
│       └─ Есть питьё (vodka/energy_drink)?
│           └─ ✅ Выпить
│
├─ УРОВЕНЬ ВЫЖИВАНИЯ
│   └─ Усталость ≥ 75?
│       └─ ✅ Запланировать sleep(6 часов = 360 тиков)
│          → event: sleep_started
│
├─ УРОВЕНЬ ТОРГОВЛИ
│   │  (проверяется, если в инвентаре есть хотя бы один артефакт)
│   │
│   ├─ Торговец в текущей локации?
│   │   └─ ✅ Продать все артефакты торговцу (_bot_sell_to_trader)
│   │      → event: bot_sold_artifact (по одному на каждый артефакт)
│   │      → memory stalker: {type:"trade_sell", money_gained, items_sold, trader_id}
│   │      → memory trader:  {type:"trade_buy",  money_spent,  items_bought, stalker_id}
│   │
│   └─ Нет торговца здесь, НО global_goal == "get_rich"?
│       └─ Есть ближайший торговец (BFS по графу)?
│           └─ ✅ travel → локация торговца (1 хоп за раз)
│              → memory: {type:"decision", destination, artifacts_count}
│
└─ УРОВЕНЬ ЦЕЛИ
    │
    ├─ global_goal == "get_rich"  ←  СПЕЦИАЛЬНЫЙ ПУТЬ (обходит проверку wealth)
    │   │  Семиступенчатая цепочка "get_rich" (см. секцию 4b)
    │   └─ ✅ Выполнить текущий шаг цепочки
    │
    ├─ Рассчитать wealth = money + Σ item.value
    │
    ├─ wealth < material_threshold?  →  ФАЗА 1: Накопление ресурсов
    │   │  (current_goal = "gather_resources")
    │   │
    │   ├─ G1: Артефакт лежит в текущей локации?
    │   │   └─ ✅ Подобрать артефакт (artifact_picked_up)
    │   │      → memory: {type:"pickup", artifact_type, artifact_value, location_id}
    │   │
    │   ├─ G2: В локации есть аномалии И rng < 0.5?
    │   │   └─ ✅ Запланировать explore(30 тиков)
    │   │      → event: exploration_started
    │   │
    │   ├─ G3: Есть открытые соседи И rng < 0.7?
    │   │   └─ ✅ travel → сосед с макс. score (1 хоп)
    │   │      score = anomaly_activity×2 + artifact_count×3
    │   │
    │   ├─ G4: rng < 0.4?
    │   │   └─ ✅ Запланировать explore(30 тиков)
    │   │
    │   └─ Fallback: ждать (action_used = True)
    │
    └─ wealth ≥ material_threshold?  →  ФАЗА 2: Целевое поведение
        (current_goal = "goal_<global_goal>")
        │
        ├─ global_goal == "survive"
        │   ├─ anomaly_activity > 5 И есть безопасный сосед?
        │   │   └─ ✅ travel → безопасная локация (1 хоп)
        │   ├─ sleepiness ≥ 40?
        │   │   └─ ✅ sleep(4 часа)
        │   ├─ Артефакт в локации?
        │   │   └─ ✅ Подобрать артефакт
        │   └─ Fallback: wander (см. ниже)
        │
        ├─ global_goal == "explore_zone"
        │   ├─ Есть непосещённые соседи (по памяти о travel)?
        │   │   └─ ✅ travel → случайный непосещённый сосед (1 хоп)
        │   ├─ rng < 0.5?
        │   │   └─ ✅ explore(30 тиков)
        │   ├─ Есть соседи?
        │   │   └─ ✅ travel → случайный сосед (1 хоп)
        │   └─ Fallback: wander
        │
        ├─ global_goal == "help_others" / "find_wish" / "serve_faction"
        │   ├─ Есть соседи с однофракционными агентами?
        │   │   └─ ✅ travel → ближайшая такая локация (1 хоп)
        │   └─ Fallback: wander
        │
        └─ (общий Fallback для всех goal)
            ├─ Есть соседи И rng < 0.6?
            │   └─ ✅ travel → случайный сосед (1 хоп = wander)
            ├─ rng < 0.3?
            │   └─ ✅ explore(30 тиков)
            └─ Ждать (action_used = True)
```

---

## 4b. Цепочка «get_rich» — семь шагов

Сталкер с `global_goal="get_rich"` выполняет следующую последовательную логику
(проверяется ДО уровня wealth, т.е. сразу с первого тика):

| Шаг | Триггер | Действие | Запись в память |
|-----|---------|----------|-----------------|
| 1 | Нет артефактов нигде в инвентаре, не знает лучшей локации | Обследует карту, находит локацию с наибольшим числом/ценностью артефактов | `"Ищу ценные предметы. Лучшая локация: X."` |
| 2 | Лучшая локация найдена | travel туда (хоп за хопом) | `"Иду на X за артефактами."` |
| 3 | Прибыл, в локации есть артефакты | Поднять лучший артефакт | `"Поднял артефакт <type> (стоимость: N)."` |
| 4 | Артефактов > 1 в инвентаре | Составить план продажи | `"Планирую продать: X, Y, …"` |
| 5 | Нет торговца здесь | BFS → ближайший торговец | `"Ближайший торговец: <name> в <location>."` |
| 6 | Торговец найден | travel к торговцу (хоп за хопом) | `"Иду к торговцу <name>."` |
| 7 | Торговец в текущей локации | Продать все артефакты | `"Продал N артефактов на M денег."` |

> **Примечание**: каждый travel (шаги 2 и 6) реализован как последовательность
> одиночных хопов. Агент реально оказывается в каждой промежуточной локации.

---

## 5. Обработка scheduled_action (шаг 1 тика)

Когда у агента есть `scheduled_action`, каждый тик:

```
turns_remaining -= 1

if turns_remaining > 0:
    emit {action_type}_in_progress
    return

# Действие завершено:
scheduled_action = None
```

### Завершение travel — хоп за хопом

Каждый хоп — это **один edge графа**. `scheduled_action` содержит:

```
{
  "type": "travel",
  "turns_remaining": <минут до следующей локации>,
  "target_id": "<ID следующей локации>",          ← ближайший хоп
  "final_target_id": "<ID конечной локации>",      ← конечная цель
  "remaining_route": ["<hop2>", "<hop3>", ...],   ← ещё не пройденные хопы
}
```

При завершении хопа:
- Агент **физически перемещается** в `target_id` (меняется `location_id`)
- Снимается урон от аномалий в `target_id` (1/4 урона аномалии)
- Если `remaining_route` не пуст → автоматически планируется следующий хоп
  (event `travel_hop_completed`)
- Если `remaining_route` пуст → маршрут завершён (event `travel_completed`),
  запись в `memory`, `scheduled_action = None`
- Если `hp ≤ 0` → `is_alive = False`, event `agent_died`

### Завершение explore (30 тиков)
Вызывается `_resolve_exploration`:

```
rng.roll < 0.4  →  "нашёл что-то"
    rng2 < 0.4 AND есть аномалии  →  артефакт → в инвентарь
    иначе                         →  предмет (медицинский/расходник/патроны) → в инвентарь

Шанс встречи с аномалией:
    rng < 0.15 × (anomaly_activity / 10)
        → урон от случайной аномалии в локации
        → если hp ≤ 0 → смерть
```

### Завершение sleep (hours × 60 тиков)
- `hp += min(15 × hours, max_hp - hp)`
- `radiation -= 5 × hours` (минимум 0)
- `sleepiness = 0`
- Запись в `memory`

---

## 6. Деградация потребностей (раз в 60 тиков = каждый час)

Выполняется в шаге 2 для **всех живых агентов** (и НПЦ, и игроков):

```
hunger    += 3   (кап: 100)
thirst    += 5   (кап: 100)
sleepiness+= 4   (кап: 100)

if thirst ≥ 80  → hp -= 2
if hunger ≥ 80  → hp -= 1
if hp ≤ 0       → is_alive = False, event agent_died (starvation_or_thirst)
```

---

## 7. Пример жизненного цикла спавненного НПЦ

Допустим, НПЦ спавнится в локации C1, `global_goal="get_rich"`, стартовые деньги 300 RU.

| Тики | Событие |
|---|---|
| 1 | **get_rich шаг 1**: обследует карту, находит лучшую артефактную локацию (напр. G3) |
| 2 | **get_rich шаг 2**: планирует travel C1→C2→C4→G3 (хоп за хопом) |
| 2–8 | `travel_in_progress` (хоп C1→C2, 8 мин) |
| 8 | `travel_hop_completed`: агент теперь в C2 |
| 9–18 | `travel_in_progress` (хоп C2→C4, 10 мин) |
| 18 | `travel_hop_completed`: агент теперь в C4 |
| … | … следующие хопы … |
| N | `travel_completed`: агент прибыл в G3 |
| N+1 | **get_rich шаг 3**: артефакт есть — поднимает, memory: `pickup` |
| N+2 | **get_rich шаг 4**: >1 артефакта — планирует продажу, memory: `decision` |
| N+3 | **get_rich шаг 5–6**: BFS → ближайший торговец → travel к нему |
| … | `travel_hop_completed` × (количество хопов до торговца) |
| M | **get_rich шаг 7**: торговец здесь → `_bot_sell_to_trader`, memory: `trade_sell` |
| 60 | Деградация: hunger=23, thirst=25, sleepiness=14 |

## 7b. Сценарий «артефакт → продажа торговцу»

| Тик | Действие | Запись в память |
|---|---|---|
| 1 | G1: Подбирает `Soul` (2000 RU) | `{type:"pickup", artifact_type:"soul", artifact_value:2000}` |
| 2 | ТОРГОВЛЯ: артефакт в инвентаре, торговца нет, goal=get_rich → BFS → travel первым хопом | `{type:"decision", destination:"loc_C1", artifacts_count:1}` |
| 3–8 | `travel_in_progress` (первый хоп) | |
| 8 | `travel_hop_completed`: прибыл в промежуточную локацию | |
| … | Следующие хопы | |
| M | `travel_completed`: прибыл к торговцу; тут же `_run_bot_action`: торговец ЗДЕСЬ → продажа | stalker: `{type:"trade_sell", money_gained:1200, items_sold:["soul"]}` |
| | | trader: `{type:"trade_buy", money_spent:1200, items_bought:["soul"]}` |

---

## 8. Когда НПЦ может погибнуть

1. **Аномалии при перемещении** (`travel_hop_completed` / `travel_completed`) — 1/4 урона за прибытие в каждую локацию с аномалиями
2. **Аномалия при исследовании** (`exploration_completed`) — полный урон, шанс 15% × (anomaly_activity/10)
3. **Голод/жажда** (критические 80+, каждый час)
4. Косвенно — если HP упал до 0 от любого источника и нет предметов лечения

---

## 9. Память агента и торговца

В `memory[]` записываются последние 50 событий. Каждая запись содержит:

```json
{
  "world_turn": 42,
  "world_day": 1,
  "world_hour": 7,
  "world_minute": 22,
  "type": "<тип>",
  "title": "...",
  "summary": "...",
  "effects": { ... }
}
```

### Типы записей в памяти

| `type` | Кто пишет | Что фиксируется |
|---|---|---|
| `travel` | сталкер | Прибытие в конечную локацию, урон от аномалий |
| `explore` | сталкер | Результат исследования (предметы, артефакты, встречи с аномалиями) |
| `sleep` | сталкер | Часы сна, восстановление HP и радиации |
| `pickup` | сталкер-бот | Подобран артефакт с пола: `{artifact_type, artifact_value, location_id}` |
| `decision` | сталкер-бот | Решение идти к торговцу: `{destination, artifacts_count}` |
| `trade_sell` | сталкер-бот | Продажа артефактов: `{money_gained, items_sold[], trader_id}` |
| `trade_buy` | торговец | Покупка у сталкера: `{money_spent, items_bought[], stalker_id}` |

### Сценарий "artifact → sell": полная цепочка памяти

```
Память сталкера после полного цикла:
  [0] type="pickup",     title="Поднял артефакт soul...",          effects={artifact_type:"soul", ...}
  [1] type="decision",   title="Решил продать добычу",             effects={destination:"loc_C1", artifacts_count:1}
  [2] type="travel",     title="Arrived at Деревня...",            effects={damage_taken:0}
  [3] type="trade_sell", title="Продал soul торговцу Sid...",      effects={money_gained:1200, items_sold:["soul"], ...}

Память торговца:
  [0] type="trade_buy",  title="Купил soul у сталкера Test Stalker", effects={money_spent:1200, ...}
```

Память используется алгоритмом `explore_zone`-цели для отслеживания посещённых локаций.

---

## 10. Связанные файлы

| Файл | Роль |
|---|---|
| `generators/zone_generator.py` | Создаёт агентов (`_make_stalker_agent`) и торговцев (с `memory: []`) |
| `generators/fixed_zone_map.py` | Фиксированный граф 32 локаций (C1-C6, G1-G6, A1-A6, D1-D6, S1-S8) с travel_time для каждого ребра |
| `rules/tick_rules.py` | `tick_zone_map()` + `_run_bot_action()` + `_bot_sell_to_trader()` + `_process_scheduled_action()` + `_bot_schedule_travel()` |
| `rules/world_rules.py` | Команды: `travel`, `sleep`, `explore`, `take_control`, `debug_spawn_stalker`, `debug_spawn_trader` |
| `balance/items.py` | Типы предметов, `HEAL_ITEM_TYPES`, `FOOD_ITEM_TYPES`, `DRINK_ITEM_TYPES` |
| `balance/artifacts.py` | Типы артефактов и их ценность (ключи = `_ARTIFACT_ITEM_TYPES`) |
| `balance/anomalies.py` | Типы аномалий и урон |
