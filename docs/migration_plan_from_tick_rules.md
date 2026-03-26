# Migration Plan from `tick_rules` to NPC Decision Architecture v2

## Назначение документа

Этот документ описывает **поэтапную миграцию** текущей логики принятия решений NPC из существующего world-tick каскада в новую архитектуру:

- `Perceive`
- `Evaluate`
- `Intend`
- `Plan`
- `Act`

Документ рассчитан на реальную реализацию в проекте, а не на абстрактный redesign.

Он должен помочь:
- не сломать уже работающий геймплей;
- не делать big bang rewrite;
- постепенно заменить существующий decision cascade;
- сохранить совместимость с текущими данными матчей;
- подготовить систему к дипломатии и группам.

---

# 1. Исходная точка миграции

Сейчас в проекте уже существует:
- `zone_stalkers` пакет;
- центральный world-tick loop;
- логика `scheduled_action`;
- memory-based NPC behavior;
- combat interaction logic;
- частично оформленный decision tree;
- refactor spec и addendum в `docs/`.

Основная текущая проблема:
> логика выбора поведения сосредоточена в одном procedural cascade, который неудобно расширять под дипломатию, отношения и группы.

---

# 2. Цели миграции

Миграция считается успешной, если система сможет:

1. объяснить, **почему** NPC сделал выбор;
2. показать `NeedScores` агента;
3. показать `dominant intent`;
4. показать `active plan`;
5. позволить NPC:
   - вступать в диалог,
   - обмениваться памятью,
   - предлагать объединение в группу,
   - следовать групповому плану;
6. сохранить совместимость с текущими long actions;
7. не сломать боёвку, торговлю, память и глобальные цели.

---

# 3. Непереговорные ограничения

Следующие вещи не переписываются “с нуля”:

- `wealth gate` остаётся;
- world tick остаётся;
- `scheduled_action` не удаляется сразу;
- существующая память остаётся источником поведения;
- существующие глобальные цели не отменяются;
- миграция идёт поэтапно, с временными bridge-механизмами.

---

# 4. Стратегия миграции

Главный принцип:

> Сначала оборачиваем старую систему в новые сущности, потом переносим логику по слоям, и только в конце заменяем старый cascade.

То есть миграция идёт в 3 больших фазы:

1. **Compatibility phase**
2. **Parallel phase**
3. **Cutover phase**

---

# 5. Phase 1 — Compatibility Layer

## Цель
Ввести новые сущности без изменения реального поведения.

## Результат
Проект всё ещё живёт по старой логике, но уже умеет строить:
- `AgentContext`
- `NeedScores`
- `Intent`
- `Plan`
- `RelationState`
- `GroupState`

## Задачи

### 5.1. Создать новые модели
Создать папку:

```text
backend/app/games/zone_stalkers/decision/models/
```

И положить туда:
- `agent_context.py`
- `need_scores.py`
- `intent.py`
- `plan.py`
- `relation_state.py`
- `group_state.py`

### 5.2. Создать context builder
Файл:
```text
decision/context_builder.py
```

Функция:
```python
build_agent_context(agent: dict, state: dict) -> AgentContext
```

Пока она должна только собрать данные из текущего runtime state.

### 5.3. Создать bridge для `scheduled_action`
Файл:
```text
decision/bridges.py
```

Нужны функции:
- `plan_from_scheduled_action(agent) -> Plan | None`
- `scheduled_action_from_plan_step(step: PlanStep) -> dict`

### 5.4. Создать explain/debug слой
Файл:
```text
decision/debug/explain_intent.py
```

Даже если intent пока вычисляется по старому cascade, должен появиться единый explain output.

## Definition of Done
- код собирается;
- старая логика не изменилась;
- для любого NPC можно построить `AgentContext`;
- для любого `scheduled_action` можно построить временный `Plan`;
- explain output уже существует.

---

# 6. Phase 2 — NeedScores поверх старой логики

## Цель
Сделать слой оценки потребностей, но не менять ещё сам pipeline исполнения.

## Результат
Перед старым выбором действия система может вычислить и показать давления потребностей.

## Задачи

### 6.1. Реализовать `decision/needs.py`
Функция:
```python
evaluate_needs(ctx: AgentContext, state: dict) -> NeedScores
```

Она должна считать:
- survive_now
- heal_self
- eat
- drink
- sleep
- reload_or_rearm
- get_rich
- hunt_target
- unravel_zone_mystery
- avoid_emission
- trade
- negotiate
- maintain_group
- help_ally
- join_group
- leave_zone

### 6.2. Встроить NeedScores в debug output
Для каждого NPC лог/отладка должны показывать:
- top 3 need scores;
- почему они такие.

### 6.3. Не менять решение действия
На этом этапе старая `_run_bot_action_inner` всё ещё принимает решение как раньше.

## Definition of Done
- NeedScores вычисляются стабильно;
- нет изменения поведения NPC;
- можно сравнить старое решение и top drives.

---

# 7. Phase 3 — Intent Layer в shadow mode

## Цель
Ввести `Intent` как новую сущность, но пока без жёсткого влияния на реальный action selection.

## Результат
Система умеет параллельно:
- считать старое решение,
- строить `dominant intent`.

## Задачи

### 7.1. Реализовать `decision/intents.py`
Функция:
```python
select_intent(ctx: AgentContext, needs: NeedScores, state: dict) -> Intent
```

### 7.2. Shadow comparison mode
Добавить debug-mode, который показывает:
- старое решение из cascade;
- новый selected intent;
- совпадают они или нет.

### 7.3. Ввести intent analytics
Нужна статистика:
- как часто старое решение и новый intent расходятся;
- где именно самые частые расхождения.

## Definition of Done
- intent строится для всех NPC;
- intent не ломает существующий gameplay;
- расхождения с current behavior можно увидеть и анализировать.

---

# 8. Phase 4 — Plan Layer

## Цель
Сделать короткие планы поверх intents.

## Результат
У агента появляется не только intent, но и явный `Plan`.

## Задачи

### 8.1. Реализовать `decision/planner.py`
Функция:
```python
build_plan(ctx: AgentContext, intent: Intent, state: dict) -> Plan
```

### 8.2. Научить planner строить минимальные планы для:
- travel
- sleep
- heal self
- buy item
- sell item
- hunt intel
- flee
- ask for intel
- leave zone

### 8.3. Сделать bridge к текущему `scheduled_action`
Исполнитель всё ещё может работать через `scheduled_action`, но planner уже должен быть источником истины.

## Definition of Done
- для основных intents строится Plan;
- `scheduled_action` можно сгенерировать из PlanStep;
- старый pipeline ещё не удалён.

---

# 9. Phase 5 — Cutover: world decisions

## Цель
Заменить старый world-decision cascade на новый pipeline:
`Context -> Needs -> Intent -> Plan -> Execute`

## Результат
Обычные мировые решения NPC больше не принимаются старой процедурной функцией.

## Задачи

### 9.1. Вынести старую `_run_bot_action_inner` в legacy модуль
Например:
```text
rules/legacy_decision_logic.py
```

### 9.2. В world tick переключить вызов на:
1. build context
2. evaluate needs
3. select intent
4. build plan
5. execute current step

### 9.3. Оставить legacy fallback
На время стабилизации:
- если новый planner не смог построить plan,
- система может использовать legacy fallback.

## Definition of Done
- обычный world AI живёт через новый pipeline;
- legacy работает только как fallback;
- поведение сохраняется на базовых сценариях.

---

# 10. Phase 6 — Social Layer

## Цель
Добавить полноценную социальную модель без генеративной сложности.

## Результат
У NPC появляются отношения, а диалог становится отдельной механикой.

## Задачи

### 10.1. Реализовать storage отношений
Файлы:
```text
decision/social/relations.py
decision/social/relation_updates.py
```

### 10.2. Ленивая инициализация
Если отношения между двумя NPC ещё не созданы:
- они считаются нейтральными.

### 10.3. Реализовать `DialogueSession`
Файлы:
```text
decision/social/dialogue.py
decision/social/memory_exchange.py
decision/social/diplomacy.py
```

### 10.4. Поддержать MVP-операции диалога
- ask_for_intel
- exchange_memories
- offer_trade
- propose_grouping
- warn_about_threat
- ask_for_help

## Definition of Done
- NPC могут начать короткий структурированный диалог;
- память может обмениваться между NPC;
- отношения меняются по событиям и диалогам.

---

# 11. Phase 7 — Groups Layer

## Цель
Добавить минимальные группы NPC с leader/member логикой.

## Результат
NPC могут объединяться и следовать общему плану.

## Задачи

### 11.1. Реализовать `GroupState`
Файлы:
```text
decision/groups/group_state.py
decision/groups/hierarchy.py
```

### 11.2. Реализовать правила создания группы
Условия:
- схожая глобальная цель;
- достаточный trust/friendly attitude;
- отсутствие сильной вражды;
- положительная выгода от объединения.

### 11.3. Реализовать базовые group needs
Файлы:
```text
decision/groups/group_needs.py
decision/groups/group_intents.py
decision/groups/group_planner.py
```

### 11.4. MVP group behavior
- leader/member
- shared_goal
- follow_group_plan
- regroup
- protect_weak_member
- share_intel

## Definition of Done
- 2 NPC могут образовать группу;
- группа имеет лидера;
- группа может менять план, если один из участников в критическом состоянии.

---

# 12. Phase 8 — Combat integration with social/group model

## Цель
Не переписать боёвку, а встроить её в новую архитектуру.

## Результат
Бой использует:
- отношения,
- fear,
- loyalty,
- group membership,
- retreat visibility,
- temporary alliance possibilities.

## Задачи

### 12.1. Боевые intents
Файлы:
```text
decision/combat/combat_intents.py
decision/combat/target_selection.py
decision/combat/retreat_logic.py
```

### 12.2. Связь с social model
В бою должны учитываться:
- кто ally,
- кто hostile,
- кто neutral,
- кто temporary ally,
- можно ли предложить ceasefire/truce later.

### 12.3. Связь с groups
Если NPC состоит в группе:
- лидер может задавать боевой приоритет;
- член группы может выбрать support/follow behavior;
- emergency needs участника могут корректировать group combat behavior.

## Definition of Done
- бой совместим с social data;
- групповой член учитывает group context;
- retreat и memory корректно встраиваются в social history.

---

# 13. Порядок изменения файлов

Ниже — практический порядок внедрения.

## Step 1
Создать:
- `decision/models/*`
- `decision/context_builder.py`
- `decision/bridges.py`

## Step 2
Создать:
- `decision/needs.py`
- `decision/debug/explain_intent.py`

## Step 3
Создать:
- `decision/intents.py`
- shadow mode comparison

## Step 4
Создать:
- `decision/planner.py`
- `decision/executors.py`

## Step 5
Переподключить world tick:
- сначала partial
- потом full cutover

## Step 6
Создать:
- `decision/social/*`

## Step 7
Создать:
- `decision/groups/*`

## Step 8
Подтянуть:
- `decision/combat/*`

---

# 14. Миграционные мосты

Чтобы миграция не сломала текущие сейвы/матчи, нужны временные мосты.

## 14.1. `scheduled_action <-> PlanStep`
Пока migration не закончена:
- оба формата допустимы.

## 14.2. `memory -> known_entities / known_locations`
Новая архитектура должна читать старую память как источник knowledge.

## 14.3. `friends/enemies -> RelationState`
Старые friend/enemy структуры нужно читать как seed для social model.

## 14.4. `group = None` by default
Все существующие NPC изначально без группы.

---

# 15. Риски миграции

## 15.1. Поведение NPC неожиданно изменится
Новый intent-layer может принять другое решение, чем старый cascade.

### Контрмера
Shadow mode + comparison logs.

## 15.2. Планирование окажется слишком тяжёлым
Planner может стать дорогим по CPU.

### Контрмера
Короткие планы, lazy rebuild, reuse current plan if still valid.

## 15.3. Отношения вызовут O(N²)
### Контрмера
Ленивая инициализация relations.

## 15.4. Groups резко усложнят AI
### Контрмера
Начать с group-of-2 и leader/member only.

---

# 16. Regression strategy

## 16.1. Golden scenarios
Нужны золотые сценарии:
- trader resupply
- low hp -> heal
- emission escape
- hunt target by intel
- flee and memory
- sell artifact
- leave zone after completed goal

## 16.2. New scenarios
Добавить:
- ask_for_intel dialogue
- exchange_memories
- propose_grouping
- group leader death
- weak member changes group plan

---

# 17. Критерии готовности миграции

Миграция считается успешной, когда:

1. ordinary world decisions работают через новый pipeline;
2. explain output показывает:
   - context
   - top needs
   - dominant intent
   - active plan
   - executed step
3. diplomacy работает на MVP-уровне;
4. 2 NPC могут создать группу;
5. group needs реально влияют на маршрут;
6. old legacy cascade больше не нужен как основной путь.

---

# 18. Что можно отложить

Не включать в первую волну:
- генеративные тексты диалогов;
- сложную политику фракций;
- голосование в группах;
- разветвлённую групповую тактику;
- продвинутые социальные манипуляции;
- full GOAP planner.

---

# 19. Что давать Copilot поэтапно

## Первая серия задач
- create AgentContext dataclass
- create NeedScores dataclass
- create Intent dataclass
- create Plan / PlanStep dataclasses
- create plan_from_scheduled_action bridge

## Вторая серия задач
- implement evaluate_needs()
- implement select_intent()
- implement explain_intent()

## Третья серия задач
- implement build_plan()
- implement execute_plan_step()
- wire pipeline into world tick in shadow mode

## Четвёртая серия задач
- implement RelationState storage
- implement DialogueSession
- implement memory exchange

## Пятая серия задач
- implement GroupState
- implement leader/member groups
- implement group needs

---

# 20. Итог

Если коротко:

> Миграция должна идти не через переписывание всей логики сразу, а через постепенное оборачивание текущего `tick_rules` каскада в новые сущности `Context`, `Needs`, `Intent`, `Plan`, с последующим переключением на новый pipeline и только потом — добавлением social/group mechanics.

Это даст:
- контролируемую миграцию;
- минимальный риск поломки MVP;
- хорошую совместимость с текущим кодом;
- реальную основу для дипломатии и групп.
