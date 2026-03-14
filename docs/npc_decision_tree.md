# Дерево принятия решений НПЦ — Zone Stalkers

> Документ описывает **актуальное** поведение НПЦ-сталкера на основе кода в
> `backend/app/games/zone_stalkers/rules/tick_rules.py` и
> `backend/app/games/zone_stalkers/generators/zone_generator.py`.
>
> Последнее обновление: соответствует ветке `copilot/update-time-step-and-debug-menu`.

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
global_goal       ← "survive" | "get_rich" | "explore" | "serve_faction"
current_goal      ← строка, отражает текущий выбор в цикле тика
risk_tolerance    ← 0.2–0.9
material_threshold← 500–3000 RU  (порог накопления перед переходом к цели)
scheduled_action  ← активное длительное действие или null
action_queue[]    ← очередь следующих действий
memory[]          ← журнал последних 50 событий агента
```

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
- `global_goal` — случайный из 4 вариантов
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
│   │       │   └─ ✅ Запустить travel → безопасная локация
│   │       └─ Нет соседей → ждать (action_used = True)
│   │
│   ├─ Голод ≥ 70?
│   │   └─ Есть еда (bread/energy_drink)?
│   │       └─ ✅ Съесть
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
└─ УРОВЕНЬ ЦЕЛИ
    │
    ├─ Рассчитать wealth = money + Σ item.value
    │
    ├─ wealth < material_threshold?  →  ФАЗА 1: Накопление ресурсов
    │   │  (current_goal = "gather_resources")
    │   │
    │   ├─ G1: Артефакт лежит в текущей локации?
    │   │   └─ ✅ Подобрать артефакт (artifact_picked_up)
    │   │
    │   ├─ G2: В локации есть аномалии И rng < 0.5?
    │   │   └─ ✅ Запланировать explore(30 тиков)
    │   │      → event: exploration_started
    │   │
    │   ├─ G3: Есть открытые соседи И rng < 0.7?
    │   │   └─ ✅ travel → сосед с макс. score
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
        │   │   └─ ✅ travel → безопасная локация
        │   ├─ sleepiness ≥ 40?
        │   │   └─ ✅ sleep(4 часа)
        │   ├─ Артефакт в локации?
        │   │   └─ ✅ Подобрать артефакт
        │   └─ Fallback: wander (см. ниже)
        │
        ├─ global_goal == "get_rich"
        │   ├─ Артефакт в локации?
        │   │   └─ ✅ Подобрать артефакт
        │   ├─ Аномалии И rng < 0.65?
        │   │   └─ ✅ explore(30 тиков)
        │   ├─ Есть соседи?
        │   │   └─ ✅ travel → сосед с макс. anomaly_activity
        │   └─ Fallback: wander
        │
        ├─ global_goal == "explore"
        │   ├─ Есть непосещённые соседи (по памяти о travel)?
        │   │   └─ ✅ travel → случайный непосещённый сосед
        │   ├─ rng < 0.5?
        │   │   └─ ✅ explore(30 тиков)
        │   ├─ Есть соседи?
        │   │   └─ ✅ travel → случайный сосед
        │   └─ Fallback: wander
        │
        ├─ global_goal == "serve_faction"
        │   ├─ Есть соседи с однофракционными агентами?
        │   │   └─ ✅ travel → ближайшая такая локация
        │   └─ Fallback: wander
        │
        └─ (общий Fallback для всех goal)
            ├─ Есть соседи И rng < 0.6?
            │   └─ ✅ travel → случайный сосед (wander)
            ├─ rng < 0.3?
            │   └─ ✅ explore(30 тиков)
            └─ Ждать (action_used = True)
```

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

### Завершение travel
- Агент телепортируется в `target_id`
- Снимается урон от аномалий по всем промежуточным локациям (1/4 урона аномалии)
- Запись в `memory` о перемещении
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

Допустим, НПЦ спавнится в локации с `anomaly_activity=7`, `artifacts=[]`,
`global_goal="get_rich"`, `material_threshold=1500`, стартовые деньги 300 RU.

| Тики | Событие |
|---|---|
| 1–10 | Голод/жажда в норме. wealth=350 < 1500. **ФАЗА 1: накопление** |
| 1 | Аномалии есть, rng < 0.5 → `explore` на 30 тиков |
| 2–30 | `explore_in_progress` × 29 |
| 31 | `exploration_completed` — 40% шанс найти предмет или артефакт |
| 32 | Нет артефактов в локации, rng < 0.7 → `travel` к соседу с макс. score |
| 32–50 | `travel_in_progress` × N (зависит от travel_time маршрута) |
| 51 | `travel_completed` — прибыл. Возможно получил урон от аномалий |
| 52 | Если артефакт лежит — подбирает. Иначе снова explore или travel |
| 60 | Деградация: hunger=23, thirst=25, sleepiness=14 |
| … | Цикл продолжается |
| ~120 | Если wealth ≥ 1500 → **ФАЗА 2: `goal_get_rich`** |
| ~120+ | Агрессивно ищет аномальные зоны, активно исследует |
| ~700 (≈11 ч) | При отсутствии еды: hunger ≥ 70, если есть хлеб — съест |
| ~900 (≈15 ч) | sleepiness ≥ 75 → `sleep(6 ч = 360 тиков)` |
| ~900–1260 | `sleep_in_progress` |
| ~1260 | `sleep_completed`: hp +90, radiation -30, sleepiness=0 |

---

## 8. Когда НПЦ может погибнуть

1. **Аномалии при перемещении** (`travel_completed`) — 1/4 урона за каждый хоп
2. **Аномалия при исследовании** (`exploration_completed`) — полный урон, шанс 15% × (anomaly_activity/10)
3. **Голод/жажда** (критические 80+, каждый час)
4. Косвенно — если HP упал до 0 от любого источника и нет предметов лечения

---

## 9. Память агента

В `memory[]` записываются последние 50 событий. Каждая запись содержит:

```json
{
  "world_turn": 42,
  "world_day": 1,
  "world_hour": 7,
  "world_minute": 22,
  "type": "travel" | "explore" | "sleep",
  "title": "Travelled to Dark Valley Camp",
  "summary": "Arrived after a long journey through the Zone.",
  "effects": { "damage_taken": 15 }
}
```

Память используется алгоритмом `explore`-цели для отслеживания посещённых локаций.

---

## 10. Связанные файлы

| Файл | Роль |
|---|---|
| `generators/zone_generator.py` | Создаёт агентов через `_make_stalker_agent()` |
| `rules/tick_rules.py` | `tick_zone_map()` + `_run_bot_action()` + `_process_scheduled_action()` |
| `rules/world_rules.py` | Команды (`travel`, `sleep`, `explore`, `take_control`, `debug_spawn_stalker`) |
| `balance/items.py` | Типы предметов, `HEAL_ITEM_TYPES`, `FOOD_ITEM_TYPES`, `DRINK_ITEM_TYPES` |
| `balance/artifacts.py` | Типы артефактов и их ценность |
| `balance/anomalies.py` | Типы аномалий и урон |
