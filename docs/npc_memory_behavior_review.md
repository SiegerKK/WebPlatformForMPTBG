# NPC Memory and Behavior Review
## Refactoring notes for Copilot

## Контекст

Этот документ описывает проблемы, замеченные в новых логах NPC после внедрения новой системы принятия решений.

Анализ основан на логах:
- `Сталкер #1`
- `Сталкер #2`
- `Сталкер #3`
- `Сталкер #4`
- `Сталкер #5`

Цель документа:
- зафиксировать замеченные проблемы;
- объяснить, почему они важны;
- предложить варианты исправления;
- дать Copilot понятный список направлений для доработки.

---

# 1. Общий вывод

Новая архитектура уже даёт заметный прогресс:

- у NPC появились понятные intents;
- память стала богаче;
- маршруты и наблюдения читаются лучше;
- поведение стало менее хаотичным, чем раньше.

Но в логах всё ещё видно несколько системных проблем:

1. **состояние NPC может противоречить само себе**;
2. **`current_goal` не всегда соответствует реальному поведению**;
3. **память слишком шумная и дублирует однотипные записи**;
4. **критические потребности выживания не всегда доминируют достаточно жёстко**;
5. **некоторые observations почти не влияют на дальнейшее поведение**;
6. **некоторые intents слишком расплывчаты и плохо объясняют реальные действия NPC**.

---

# 2. Проблема: противоречивое глобальное состояние NPC

## Симптом
В логах видно, что у NPC могут одновременно быть выставлены состояния:

- `global_goal_achieved = true`
- `has_left_zone = true`

но при этом NPC:
- остаётся живым в мире,
- продолжает принимать решения,
- продолжает перемещаться,
- продолжает исследовать,
- продолжает реагировать на выброс.

Это видно особенно на примере `Сталкер #5`.

## Почему это проблема
Это ломает жизненный цикл агента.

Если NPC:
- уже достиг цели,
- уже покинул Зону,

то он не должен продолжать обычный decision loop.

Иначе:
- логи становятся недостоверными;
- цели перестают быть финальным состоянием;
- explainability ухудшается;
- появляются странные комбинации intents и flags.

## Предлагаемое решение

### Вариант A — жёсткое исключение из мира
Если:
- `has_left_zone == true`

то NPC должен:
- исключаться из активного мира;
- не участвовать в следующем tick loop;
- не получать новые intents;
- не писать новую память, кроме финальной записи.

### Вариант B — staged exit state
Ввести отдельные состояния:
- `goal_achieved`
- `exiting_zone`
- `left_zone`

И правила:
- `goal_achieved` — цель достигнута, но NPC ещё может двигаться к выходу;
- `exiting_zone` — NPC уже committed to exit;
- `left_zone` — NPC полностью выведен из мира.

### Рекомендуемый путь
Использовать **вариант B**, потому что он лучше согласуется с текущей моделью long actions.

---

# 3. Проблема: `current_goal` не соответствует реальному поведению

## Симптом
В логах часто видно:
- `current_goal = emergency_heal`

но фактические действия NPC — это:
- `resupply`
- `travel`
- `explore`
- `get_rich`
- `wait_in_shelter`

То есть `current_goal` не выглядит надёжным отражением того, чем NPC реально занят.

## Почему это проблема
`current_goal` должен быть:
- полезен для отладки;
- полезен для UI;
- полезен для explainability.

Если он живёт отдельно от реального intent/plan pipeline, он становится вводящим в заблуждение.

## Предлагаемое решение

### Вариант A — derive `current_goal` from `dominant_intent`
Вообще не хранить `current_goal` отдельно как независимую сущность.

Правило:
- `current_goal` = проекция текущего dominant intent.

Например:
- `heal_self` -> `emergency_heal`
- `seek_food` -> `restore_needs`
- `hunt_target` -> `kill_target`
- `follow_group_plan` -> `group_objective`

### Вариант B — хранить отдельно, но жёстко синхронизировать
Если `current_goal` всё же нужен как поле state:
- обновлять его только в одном месте;
- пересчитывать после выбора intent;
- запрещать ручные разрозненные изменения из разных веток кода.

### Рекомендуемый путь
Использовать **вариант A**:
- `dominant_intent` — источник истины;
- `current_goal` — derived/debug field.

---

# 4. Проблема: память слишком шумная

## Симптом
В логах очень много повторяющихся записей вида:
- “вижу персонажей”
- “прошёл через локацию”
- “укрываюсь от выброса”
- “решил укрываться от выброса”

Особенно это заметно в shelter-сценариях:
на каждом тике пишутся почти одинаковые записи.

## Почему это проблема
Память NPC должна быть:
- полезной для будущих решений;
- компактной;
- смысловой.

Сейчас же она местами превращается в:
- debug log каждого микрошага,
а не в долговременную память агента.

Это приводит к:
- раздуванию memory;
- сложному поиску действительно важных записей;
- ухудшению explainability.

## Предлагаемое решение

### Вариант A — разделить Memory и Debug Event Log
Нужно ввести два потока данных:

1. `memory`
   - только смысловые записи;
   - используются в принятии решений.

2. `debug_log`
   - подробный поток каждого шага;
   - используется для отладки и анализа.

### Вариант B — memory compaction
Если полное разделение пока рано, тогда ввести сжатие памяти.

Примеры:
- повторяющиеся shelter-записи объединять;
- repeated observation “вижу тех же NPC в той же локации” не писать каждый тик;
- repeated travel hop events можно агрегировать.

### Рекомендуемый путь
Идти поэтапно:
1. сначала ввести **memory importance tiers**;
2. потом отделить `debug_log` от `memory`.

---

# 5. Проблема: дублирование intent/action записей при shelter behavior

## Симптом
Во время укрытия от выброса NPC пишет:
- одну запись о выборе решения,
- вторую запись о самом укрытии,
и это повторяется на каждом тике.

## Почему это проблема
Это ломает семантику:
- решение выбирается один раз;
- действие длится несколько тиков;
- значит память не должна писать “новое решение” на каждом тике.

## Предлагаемое решение

### Нужна модель lifecycle для long action:

#### Правильная схема:
1. `decision`
   - выбрал `seek_shelter_from_emission`
2. `action_start`
   - начал ожидание в укрытии
3. `action_progress`
   - optional debug only
4. `action_end`
   - переждал выброс / вышел из укрытия

### Практически
Для `wait_in_shelter`:
- в memory писать только `decision` + `action_start`;
- repeated waiting ticks писать только в debug log, не в memory.

---

# 6. Проблема: survival logic недостаточно жёсткая

## Симптом
NPC в очень плохом состоянии всё ещё может:
- исследовать аномалии,
- продолжать охоту,
- продолжать resupply через рискованное поведение,
- не переключаться немедленно в режим спасения.

Особенно это видно у `Сталкер #5`, где одновременно:
- низкое HP,
- максимум hunger/thirst/sleepiness,
- плохое снаряжение,
но поведение остаётся слишком “активным”.

## Почему это проблема
Новая система needs/intents должна делать survival drives **доминантными в критике**.

Если этого не происходит:
- NPC выглядит неразумным;
- roleplay ломается;
- decisions противоречат состоянию тела.

## Предлагаемое решение

### Вариант A — hard override thresholds
Ввести жёсткие пороги:

```text
if hp <= 15:
    only intents in {heal_self, escape_danger, seek_shelter, seek_medkit}

if thirst >= 90 or hunger >= 90 or sleepiness >= 90:
    strongly suppress {explore, hunt_target, get_rich}
```

### Вариант B — multiplicative suppression
Дать survival drives право подавлять другие веса:

```text
effective_hunt_target = hunt_target * (1 - survive_now)
effective_get_rich    = get_rich * (1 - survive_now)
```

### Рекомендуемый путь
Использовать **оба подхода**:
- hard override для крайних состояний;
- multiplicative suppression для промежуточных.

---

# 7. Проблема: completed goals не гасят связанные drives

## Симптом
Даже после достижения цели в top needs/intents может продолжать всплывать:
- `hunt_target`
- или другие goal-driven pressures

Это видно в логах `Сталкер #5`.

## Почему это проблема
После `global_goal_achieved = true` соответствующий goal-drive должен:
- обнуляться;
- либо переводиться в `leave_zone`.

Иначе NPC продолжает психологически жить в уже завершённой цели.

## Предлагаемое решение

### Правило
Если `global_goal_achieved == true`, то:
- drive, соответствующий этой цели, становится `0.0`;
- вместо него поднимается `leave_zone`.

### Пример
```text
if global_goal == "kill_stalker" and global_goal_achieved:
    hunt_target = 0.0
    leave_zone = max(leave_zone, 0.8)
```

---

# 8. Проблема: observations слишком слабо влияют на поведение

## Симптом
NPC видят:
- других сталкеров,
- цели,
- движение в соседних локациях,

но часто это почти не меняет дальнейший plan, если не срабатывает специальный hardcoded branch.

## Почему это проблема
Perception уже есть, но exploitation perception ещё слабое.

NPC много узнаёт о мире, но мало это использует.

## Предлагаемое решение

### Вариант A — observation-to-intent hooks
Для отдельных наблюдений добавить прямые реактивные правила:

- увидел цель -> поднять `hunt_target`
- увидел ослабленного врага -> поднять `attack_opportunity`
- увидел союзника в плохом состоянии -> поднять `help_ally`
- увидел торговца -> поднять `trade` / `resupply`

### Вариант B — observation scoring layer
После perception строить не только knowledge, но и список:
- `opportunities`
- `threats`
- `social hooks`

### Рекомендуемый путь
Начать с **варианта A**, а потом перейти к `opportunity/threat extraction`.

---

# 9. Проблема: intents слишком широкие и плохо объясняют действия

## Симптом
Некоторые intents, например `resupply`, слишком широкие.

В логе это выглядит так:
- intent = `resupply`
- фактический шаг = поход в аномальную зону и поиск артефактов

Формально это можно объяснить как “ресапплай через добычу ценностей”, но для explainability это слабо.

## Почему это проблема
Intent должен объяснять поведение понятно.

Если intent слишком общий:
- его трудно дебажить;
- им трудно управлять;
- трудно добавить дипломатию и групповой слой.

## Предлагаемое решение

### Разбить широкий `resupply` на подтипы
Например:
- `buy_supplies`
- `search_supplies`
- `sell_loot_for_supplies`
- `seek_medical_resupply`
- `seek_ammo_resupply`

### Правило
`Intent.kind` должен быть достаточно конкретным, чтобы по нему уже было ясно:
- NPC идёт покупать?
- собирать?
- менять?
- лутать ради продажи?

---

# 10. Проблема: в памяти смешиваются решение, действие и наблюдение

## Симптом
Система уже стала лучше, но местами всё ещё видно, что:
- decision и action пишутся слишком похоже;
- shelter behavior особенно это показывает.

## Почему это проблема
Без строгого контракта типов памяти:
- explainability ухудшается;
- потом трудно строить нормальную social/memory model;
- диалоги и обмен воспоминаниями будут передавать слишком много “шума”.

## Предлагаемое решение

### Ввести строгий memory contract

#### `decision`
Записывается, когда NPC выбрал intent или action step.

#### `action`
Записывается, когда step реально начался или завершился.

#### `observation`
Записывается, когда NPC что-то увидел/узнал/получил.

#### `state_transition`
Опционально для важных фаз:
- goal achieved
- entering shelter
- leaving zone
- group formed
- leader changed

---

# 11. Предлагаемые направления фиксов

## Fix Group A — lifecycle consistency
Нужно починить:
- `goal_achieved`
- `leave_zone`
- `has_left_zone`
- participation in tick loop

## Fix Group B — memory hygiene
Нужно:
- отделить memory от debug log;
- убрать repeated duplicate shelter decisions;
- агрегировать repeated observations.

## Fix Group C — stronger survival overrides
Нужно:
- сделать survival pressures жёстче;
- подавлять risky intents при критических needs;
- выключать completed goal drives.

## Fix Group D — better intent semantics
Нужно:
- сузить слишком широкие intents;
- лучше маппить intent -> current_goal;
- сделать intent names более объяснимыми.

## Fix Group E — observation exploitation
Нужно:
- сделать наблюдения активным источником новых pressures;
- не просто писать observation в память,
- а реально превращать её в hook для replanning.

---

# 12. Рекомендуемый порядок реализации

## Шаг 1
Починить lifecycle:
- `has_left_zone`
- `global_goal_achieved`
- removal from active tick loop

## Шаг 2
Починить survival override:
- thresholds
- suppression of risky intents

## Шаг 3
Починить memory contract:
- decision/action separation
- shelter lifecycle logging
- duplicate suppression

## Шаг 4
Починить `current_goal`
- derive from dominant intent
- убрать отдельную разрозненную установку

## Шаг 5
Починить observation-to-reaction hooks
- seen target
- seen trader
- seen ally in danger
- seen opportunity

## Шаг 6
Уточнить taxonomy intents
- разбить `resupply`
- разбить слишком широкие “service intents”

---

# 13. Regression tests, которые нужно добавить

## Test 1 — goal achieved stops hunt
Если:
- `global_goal_achieved = true`

то:
- goal-drive обнуляется;
- `leave_zone` поднимается;
- NPC не продолжает обычную hunt-активность.

## Test 2 — left zone removes agent from loop
Если:
- `has_left_zone = true`

то:
- NPC не должен строить новые intents и plans.

## Test 3 — shelter logging compaction
Во время выброса:
- один `decision`
- один `action_start`
- без spam decision на каждом тике.

## Test 4 — critical survival override
Если:
- hp <= 15
или
- hunger/thirst/sleepiness >= 90

то:
- risky intents подавляются;
- доминируют survival intents.

## Test 5 — observation triggers replanning
Если NPC видит target на той же или соседней локации:
- должен вырасти relevant need;
- должен обновиться intent/plan.

## Test 6 — current_goal mirrors dominant_intent
`current_goal` должен быть derived from active intent, а не жить отдельно.

---

# 14. Короткий итог для Copilot

Если формулировать совсем коротко:

> Новая система решений уже работает лучше, но сейчас ей не хватает консистентного lifecycle, чистого memory contract, более жёсткого survival override и более сильной связи между observation, intent и реальным поведением.

Главные практические задачи:
1. привести в порядок state lifecycle;
2. уменьшить шум памяти;
3. сделать survival доминирующим в критике;
4. синхронизировать `current_goal` с `dominant_intent`;
5. сделать observations реальным триггером replanning.
