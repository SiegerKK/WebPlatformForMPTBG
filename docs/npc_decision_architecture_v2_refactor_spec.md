# Zone Stalkers — Refactoring Spec for NPC Decision Architecture v2

## Назначение документа

Этот документ описывает **целевую архитектуру принятия решений NPC** для проекта **WebPlatformForMPTBG / zone_stalkers**.

Документ предназначен как:
- техническая основа для рефакторинга;
- guide для GitHub Copilot;
- контракт между геймдизайном и кодом;
- опорный план для последующего добавления:
  - дипломатии между сталкерами,
  - диалогов,
  - торговли как социальной интеракции,
  - объединения NPC в группы,
  - иерархии внутри групп,
  - группового контроля потребностей.

---

# 1. Почему нужен рефакторинг

Текущая система NPC уже рабочая, но логика принятия решений в основном сосредоточена в world-tick каскаде правил.  
Это удобно для быстрого роста MVP, но плохо масштабируется для:

- дипломатии;
- сложных социальных отношений;
- группового поведения;
- поддержки союзников;
- переговоров в бою;
- смены групп;
- лидерства и подчинения.

### Главная проблема текущего подхода
Слишком много разных решений принимается в одном большом procedural cascade.

### Цель рефакторинга
Не переписать всё с нуля, а **перевести текущую механику в более мощную многослойную архитектуру**, сохранив уже работающие идеи:
- память,
- долгие действия,
- world tick,
- wealth gate,
- глобальные цели,
- боевое взаимодействие.

---

# 2. Что оставляем без изменений

Следующее остаётся концептуально верным и сохраняется:

1. Мир живёт по tick-based времени.
2. У NPC есть память и она реально влияет на поведение.
3. У NPC есть глобальные цели.
4. У NPC есть долгие действия / scheduled actions / action queue.
5. Wealth gate остаётся:
   - пока wealth < material_threshold, многие NPC будут сначала укреплять материальную базу.
6. Боёвка остаётся отдельным режимом принятия решений.
7. NPC могут быть bot-controlled и human-controlled.

---

# 3. Главная идея новой архитектуры

Новая архитектура должна отвечать на 5 вопросов в строгом порядке:

1. **Что я сейчас знаю о мире?**
2. **Что для меня сейчас важнее всего?**
3. **Какое намерение я выбираю?**
4. **Какой короткий план лучше всего реализует это намерение?**
5. **Какой конкретный шаг плана я исполняю сейчас?**

То есть система должна перейти к модели:

```text
Perceive -> Evaluate -> Intend -> Plan -> Act
```

Это не означает отказ от rule-based логики.  
Наоборот: rule-based подход сохраняется, но распределяется по слоям.

---

# 4. Архитектурные слои

## 4.1. Layer 1 — Perception / AgentContext

Этот слой собирает нормализованный контекст для агента.

### На входе:
- state мира;
- текущее положение NPC;
- память NPC;
- текущие отношения;
- текущая группа;
- активный бой;
- scheduled_action;
- inventory/equipment.

### На выходе:
объект `AgentContext`.

### `AgentContext` должен включать:
- `self_state`
- `location_state`
- `visible_entities`
- `known_entities`
- `known_locations`
- `known_hazards`
- `known_traders`
- `known_targets`
- `current_commitment`
- `combat_context`
- `social_context`
- `group_context`
- `world_context`

### Задача слоя
Не принимать решений, а только привести данные к единому виду.

---

## 4.2. Layer 2 — Needs / Drives

Этот слой отвечает за оценку давления потребностей.

NPC не должен сразу выбирать действие.  
Сначала он должен понять, **что сейчас для него важно**.

### Примеры drives:
- `survive_now`
- `heal_self`
- `eat`
- `drink`
- `sleep`
- `reload_or_rearm`
- `maintain_group`
- `help_ally`
- `get_rich`
- `hunt_target`
- `unravel_zone_mystery`
- `avoid_emission`
- `trade`
- `negotiate`
- `join_group`
- `maintain_reputation`

### Формат
Каждый drive возвращается как score:

```text
NeedScores
- survive_now: 0.95
- heal_self: 0.88
- eat: 0.15
- get_rich: 0.42
- hunt_target: 0.71
- negotiate: 0.20
```

### Важное правило
Wealth gate сохраняется именно здесь:
- если wealth < material_threshold, усиливаются материальные drives;
- если wealth >= material_threshold, усиливается глобальная цель.

---

## 4.3. Layer 3 — Intent Selection

Этот слой превращает набор drives в **намерение**.

### Примеры intents:
- `escape_danger`
- `heal_self`
- `seek_food`
- `seek_water`
- `rest`
- `resupply`
- `trade`
- `loot`
- `explore`
- `hunt_target`
- `search_information`
- `negotiate`
- `assist_ally`
- `form_group`
- `follow_group_plan`
- `leave_zone`

### Почему intent нужен отдельно
Потому что:
- действие слишком мелкое;
- goal слишком большое;
- intent — это хороший средний слой.

Например:
- глобальная цель: `kill_stalker`
- текущий intent: `search_information`
- конкретный план: `идти к торговцу -> купить сведения -> пройти в локацию X`

---

## 4.4. Layer 4 — Planning

Планировщик превращает intent в короткий исполнимый план.

### Формат плана
```text
Plan
- intent
- goal_reference
- steps[]
- interruptibility
- expires_at
- confidence
```

### Примеры шагов
- `travel_to_location`
- `sleep_for_hours`
- `trade_buy_item`
- `trade_sell_item`
- `ask_for_information`
- `heal_self`
- `heal_ally`
- `start_dialogue`
- `join_combat`
- `retreat_from_combat`
- `follow_leader`
- `share_supplies`

### Связь с текущим кодом
`schedule_action` должен перестать быть “универсальной сущностью поведения” и стать:
> текущим исполняемым шагом плана.

---

## 4.5. Layer 5 — Execution

Исполнитель отвечает только за фактическое применение шага плана.

### Он должен:
- уменьшать remaining time;
- создавать игровые события;
- тратить предметы и ресурсы;
- менять location;
- наносить урон;
- писать память;
- сообщать planner-у о завершении шага или провале.

### Он НЕ должен:
- выбирать стратегию;
- выбирать мотив;
- выбирать social intent;
- решать, что важнее — еда или охота.

---

# 5. Социальная архитектура

Это новый обязательный слой для будущей дипломатии и групп.

## 5.1. Social Model

У каждого NPC должно быть не просто “friend/enemy”, а полноценное социальное описание отношений с другими агентами.

### `RelationState`
Для каждой пары `agent -> other_agent`:

- `attitude`
- `trust`
- `fear`
- `respect`
- `hostility`
- `debt`
- `faction_bias`
- `last_interaction_type`
- `last_interaction_turn`
- `shared_history_score`
- `known_reliability`

### Базовый `attitude`
- `ally`
- `friendly`
- `neutral`
- `suspicious`
- `hostile`
- `target`

### Зачем это нужно
Дипломатия невозможна, если у NPC нет модели отношений кроме “враг/не враг”.

---

## 5.2. Социальные intents

Нужно ввести intents, которые не являются чисто физическими действиями.

### Примеры:
- `start_dialogue`
- `exchange_memories`
- `ask_for_help`
- `ask_for_intel`
- `offer_trade`
- `propose_temporary_truce`
- `propose_grouping`
- `recruit_to_group`
- `leave_group`
- `challenge_leadership`
- `warn_about_threat`
- `request_healing`
- `request_supplies`

### Что это даёт
Тогда NPC может:
- не только стрелять или идти,
- но и вступать в социальное взаимодействие как самостоятельный режим поведения.

---

## 5.3. Диалоги между NPC

Диалог должен стать отдельным interaction flow.

### Для MVP диалог — это не “большое дерево реплик”, а обмен структурированными намерениями и памятью.

### Минимальные типы диалоговых операций:
- передать воспоминание;
- запросить воспоминание;
- предложить обмен;
- предложить объединиться;
- попросить помощь;
- предупредить об опасности;
- обсудить цель.

### Что может быть предметом обмена
- местоположение цели;
- знание об аномалии;
- знание о торговце;
- знание о безопасной точке;
- знание об артефакте;
- знание об отступлении противника.

### `DialogueSession`
Минимальная структура:
- `participants`
- `topic`
- `offers`
- `shared_memories`
- `relation_changes`
- `result`

---

# 6. Группы NPC

## 6.1. Базовая идея
NPC могут объединяться в группы, если:
- похожи по характеру;
- не конфликтуют по отношениям;
- имеют одну и ту же глобальную цель;
- считают кооперацию выгодной.

## 6.2. Условия создания группы
Группа может быть создана, если:
1. отношения между участниками не ниже `friendly` или есть высокий `trust`;
2. глобальные цели совпадают;
3. нет сильной конфликтной фракционной вражды;
4. участники не считают друг друга угрозой;
5. ожидаемая выгода группы положительна.

## 6.3. Что такое группа
Группа — это не просто список участников.

### `GroupState`
- `group_id`
- `name`
- `members`
- `leader_id`
- `hierarchy`
- `shared_goal`
- `shared_plan`
- `shared_memory`
- `resource_policy`
- `formation_turn`
- `status`

---

## 6.4. Иерархия группы

В группе должна быть иерархия.

### Минимальные роли:
- `leader`
- `core_member`
- `dependent_member`
- `scout`
- `support`

На MVP можно ограничиться:
- `leader`
- `member`

### Как выбирать лидера
Лидер может определяться по score:
- highest respect
- highest competence
- highest confidence
- best health/equipment
- stronger commitment to shared goal

### Что делает лидер
- формирует shared intent;
- выбирает приоритет маршрута;
- решает, когда группа отдыхает;
- решает, когда группа вступает в бой;
- может инициировать диалог / союз / отступление.

---

## 6.5. Потребности группы

Это критически важно.

Группа должна отслеживать:
- здоровье участников;
- наличие еды/воды;
- сон;
- боеспособность;
- наличие медикаментов;
- наличие оружия/патронов;
- перегруз;
- отставших или потерянных участников.

### `GroupNeeds`
- `protect_weak_member`
- `heal_member`
- `resupply_group`
- `rest_group`
- `maintain_cohesion`
- `follow_shared_goal`

### Главное правило
Группа не должна жить только потребностями лидера.  
Она должна проверять минимально допустимое состояние каждого участника.

---

## 6.6. Shared plan группы

Если группа существует, то у неё должен быть:
- `group_intent`
- `group_plan`

И отдельные NPC получают:
- либо `follow_group_plan`,
- либо отклонение от плана в экстренных случаях.

### Примеры group intents:
- `travel_as_group`
- `hunt_target_as_group`
- `trade_and_resupply`
- `rest_and_recover`
- `escape_emission`
- `escort_member`
- `defend_group`

---

# 7. Как совместить индивидуальные и групповые решения

Это важный вопрос.

Новый decision pipeline должен учитывать 2 уровня:

1. **личный уровень**
2. **групповой уровень**

### Правило:
Если NPC состоит в группе, он сначала оценивает:
- не противоречит ли его личная критическая потребность групповому плану.

### Пример:
- лидер хочет продолжать путь;
- один член группы на 10 hp и без аптечки.

Правильный результат:
- личное `survive_now` участника поднимает `group_need = protect_weak_member`
- группа должна изменить план.

То есть группа не подавляет индивидуальность полностью.

---

# 8. Целевая новая схема мира решений

Ниже — рекомендуемая итоговая схема.

```text
World Tick
  ├─ 1. Build AgentContext
  ├─ 2. Build SocialContext
  ├─ 3. Build GroupContext
  ├─ 4. Evaluate NeedScores
  ├─ 5. Select Intent
  ├─ 6. Build Plan
  ├─ 7. Execute Plan Step
  ├─ 8. Apply Events / Memory / Relation Changes
  └─ 9. Persist new state
```

Если агент состоит в группе:

```text
World Tick
  ├─ Build AgentContext
  ├─ Build GroupContext
  ├─ Evaluate personal needs
  ├─ Evaluate group needs
  ├─ Reconcile personal vs group pressures
  ├─ Select personal intent OR follow_group_plan
  └─ Execute
```

---

# 9. Предлагаемая структура модулей

```text
backend/app/games/zone_stalkers/decision/
├─ context_builder.py
├─ needs.py
├─ intents.py
├─ planner.py
├─ executors.py
├─ memory_runtime.py
├─ social/
│  ├─ relations.py
│  ├─ dialogue.py
│  ├─ diplomacy.py
│  └─ memory_exchange.py
├─ groups/
│  ├─ group_state.py
│  ├─ group_needs.py
│  ├─ group_intents.py
│  ├─ group_planner.py
│  └─ hierarchy.py
├─ combat/
│  ├─ combat_intents.py
│  ├─ combat_resolution.py
│  ├─ retreat_logic.py
│  └─ target_selection.py
└─ debug/
   ├─ explain_intent.py
   └─ explain_tree.py
```

---

# 10. Что делать с текущим кодом

Не нужно делать big bang rewrite.

## Этап 1 — Ввести новые структуры, не ломая старый код
Нужно создать:
- `AgentContext`
- `NeedScores`
- `Intent`
- `Plan`
- `RelationState`
- `GroupState`

И пока наполнять их текущими данными.

## Этап 2 — Завернуть текущий cascade в Intent/Plan interface
То есть старый код пока продолжает работать, но:
- вместо “сразу выбрал action”
- он возвращает:
  - выбранный intent,
  - выбранный plan,
  - action step.

## Этап 3 — Вынести social model
Сначала в минимальном виде:
- trust
- hostility
- fear
- faction affinity

## Этап 4 — Добавить DialogueSession
Сначала без сложных текстов:
- обмен памятью;
- запрос информации;
- предложение группы;
- предложение торговли.

## Этап 5 — Добавить GroupState
Сначала:
- создание группы;
- leader;
- members;
- shared_goal;
- follow_group_plan.

## Этап 6 — Вынести group needs
Добавить:
- protect weak member
- heal member
- resupply group
- regroup

## Этап 7 — Привязать бой к social model
Боёвка должна использовать:
- relations;
- fear;
- loyalty;
- temporary alliances;
- retreat visibility;
- surrender / truce future.

---

# 11. Инварианты новой архитектуры

Ниже — правила, которые должны быть истинны всегда.

## 11.1. Intent invariant
У агента в один момент времени только один **dominant intent**.

## 11.2. Plan invariant
Каждый scheduled action должен быть частью плана, а не возникать из воздуха.

## 11.3. Social invariant
Отношение между двумя агентами должно меняться только через:
- событие;
- диалог;
- наблюдение;
- принадлежность к группе/фракции.

## 11.4. Group invariant
Группа всегда имеет:
- хотя бы одного члена;
- leader;
- shared goal;
- актуальный статус.

## 11.5. Combat invariant
Бой не должен жить отдельно от social model:
- враг и друг определяются через отношения и боевой контекст;
- retreat, surrender, truce должны быть совместимы с social layer.

---

# 12. Как объяснить новую систему просто

Для команды и Copilot система должна объясняться так:

> NPC сначала формирует картину мира, потом оценивает свои и групповые потребности, потом выбирает намерение, затем строит короткий план и выполняет один шаг этого плана.

Если есть другой NPC рядом:
> перед действием он также учитывает социальные отношения и возможность диалога.

Если есть группа:
> он учитывает не только свои потребности, но и потребности группы, а лидер может задавать общий план.

---

# 13. Какие сущности нужно создать в коде

## 13.1. `AgentContext`
Источник истины для decision pipeline.

## 13.2. `NeedScores`
Нормализованные pressure values.

## 13.3. `Intent`
Текущее намерение NPC.

## 13.4. `Plan`
Короткий пошаговый план.

## 13.5. `RelationState`
Отношение к конкретному NPC.

## 13.6. `DialogueSession`
Открытая социальная интеракция.

## 13.7. `GroupState`
Состояние группы.

## 13.8. `GroupPlan`
Общий план группы.

---

# 14. Что должно быть первым результатом рефакторинга

После первой стадии рефакторинга проект должен уметь:

1. объяснить decision tree любого NPC;
2. показать dominant intent;
3. показать active plan;
4. показать relation map к ближайшим NPC;
5. показать, в группе NPC или нет;
6. показать, почему NPC отказался от выстрела и начал диалог/отступление;
7. показать, почему группа изменила маршрут из-за слабого члена.

Если это видно — архитектура удачная.

---

# 15. Какие задачи давать Copilot

Ниже — правильная декомпозиция для Copilot.

## Блок A — базовые модели
- создать `AgentContext`
- создать `NeedScores`
- создать `Intent`
- создать `Plan`
- создать `RelationState`
- создать `GroupState`

## Блок B — decision pipeline
- реализовать `context_builder`
- реализовать `needs evaluator`
- реализовать `intent selector`
- реализовать `planner`
- реализовать `plan step executor`

## Блок C — social
- реализовать relation storage
- реализовать relation updates from events
- реализовать `DialogueSession`
- реализовать memory exchange
- реализовать proposal to form group

## Блок D — groups
- реализовать group creation rules
- реализовать leader selection
- реализовать shared goal
- реализовать group needs
- реализовать follow_group_plan

## Блок E — integration
- подключить новую архитектуру в world tick
- адаптировать combat logic
- адаптировать trade logic
- добавить explain/debug output

---

# 16. Что НЕ нужно делать сразу

Не надо сразу делать:
- генеративные диалоги;
- сложную политику группировок;
- full GOAP planner;
- сложную репутационную экономику;
- сложную иерархию из 10 ролей;
- глубокую координацию отрядной тактики.

Сначала нужен **структурный каркас**, а не максимальная сложность.

---

# 17. Итоговое определение целевой архитектуры

Если сформулировать в одной фразе:

> Новая архитектура NPC должна отделять восприятие мира, оценку потребностей, выбор намерения, планирование и исполнение, а социальные отношения и групповое поведение должны стать отдельными первоклассными слоями системы.

Именно это даст вам:
- порядок вместо хаоса;
- расширяемость;
- лёгкое добавление дипломатии;
- лёгкое добавление групп;
- более объяснимый и устойчивый AI.

---

# 18. Следующий рекомендуемый документ

После этого документа логично сделать:

1. `npc_decision_entities_v2.md`
2. `social_model_v1.md`
3. `group_ai_v1.md`
4. `migration_plan_from_tick_rules.md`

Именно они превратят эту архитектуру в конкретный план реализации.
