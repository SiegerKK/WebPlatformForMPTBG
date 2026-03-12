# Domain Model платформы асинхронных многослойных пошаговых игр

## 1. Назначение документа

Этот документ формализует минимальную метамодель платформы.  
Его цель — зафиксировать основные сущности ядра так, чтобы:

- backend можно было разложить на стабильные модули;
- frontend мог получать предсказуемые контракты;
- авторы новых игр понимали, какие абстракции предоставляет платформа;
- Copilot получал точный контекст для генерации кода.

Этот документ описывает **не конкретную игру**, а универсальную модель платформы.

---

## 2. Базовый принцип модели

Платформа строится вокруг цепочки:

```text
Actor -> Command -> Validation -> Resolution -> Event -> Projection -> UI
```

И вокруг древовидной структуры:

```text
GameDefinition -> Match -> Context -> Entity
```

То есть:

- игра определяется как набор правил и схем;
- партия является экземпляром игры;
- партия состоит из одного или нескольких игровых контекстов;
- в контекстах живут сущности;
- акторы отправляют команды;
- команды порождают события;
- события обновляют проекции состояния;
- проекции отдаются в UI.

---

## 3. Основные сущности модели

## 3.1. GameDefinition

### Смысл
Статическое описание конкретной игры как пакета, подключаемого к платформе.

### Ответственность
- зарегистрировать типы контекстов;
- зарегистрировать archetype-ы сущностей;
- зарегистрировать доступные команды;
- зарегистрировать rule set;
- зарегистрировать генераторы;
- зарегистрировать UI-схемы;
- зарегистрировать ботов;
- объявить миграции и версию игры.

### Обязательные поля
- `game_id`
- `slug`
- `version`
- `title`
- `description`
- `supported_modes`
- `root_context_type`
- `context_definitions`
- `entity_archetypes`
- `action_definitions`
- `rulesets`
- `generator_definitions`
- `ui_schema_registry`
- `bot_registry`

### Жизненный цикл
1. Загружается при старте API/worker.
2. Регистрируется в `GameRegistry`.
3. Используется при создании матчей и контекстов.
4. Используется при валидации команд и генерации UI.

### Связи
- 1 `GameDefinition` -> много `Match`
- 1 `GameDefinition` -> много `ContextDefinition`
- 1 `GameDefinition` -> много `EntityArchetype`

---

## 3.2. Match

### Смысл
Экземпляр партии одной конкретной игры.

### Ответственность
- объединить участников;
- хранить root context;
- фиксировать фазу и статус партии;
- задавать глобальные правила видимости, seed и режим.

### Обязательные поля
- `match_id`
- `game_id`
- `game_version`
- `status`
- `seed`
- `created_by_user_id`
- `created_at`
- `started_at`
- `finished_at`
- `root_context_id`
- `mode`
- `visibility_mode`
- `settings`
- `metadata`

### Дополнительные поля
- `title`
- `is_ranked`
- `max_players`
- `current_phase`
- `winner_side_id`

### Статусы
- `draft`
- `waiting_for_players`
- `initializing`
- `active`
- `paused`
- `finished`
- `archived`
- `failed`

### Жизненный цикл
1. Создаётся пользователем или системой.
2. Присоединяются участники.
3. Инициализируется мир.
4. Запускается root context.
5. Партия переходит в active.
6. По условиям победы/поражения завершается.
7. Архивируется.

### Связи
- 1 `Match` -> 1 `GameDefinition`
- 1 `Match` -> 1 root `Context`
- 1 `Match` -> много `Context`
- 1 `Match` -> много `Participant`
- 1 `Match` -> много `Command`
- 1 `Match` -> много `Event`

---

## 3.3. Participant

### Смысл
Участник партии: игрок, бот или нейтральная сторона.

### Ответственность
- связывать пользователя или AI с матчем;
- задавать сторону, роль, права, статус;
- хранить fallback policy.

### Обязательные поля
- `participant_id`
- `match_id`
- `kind` (`human`, `bot`, `neutral`, `system`)
- `user_id` nullable
- `side_id`
- `role`
- `status`
- `joined_at`

### Важные поля
- `fallback_policy_id`
- `bot_policy_id`
- `display_name`
- `is_ready`
- `color`
- `meta`

### Статусы
- `invited`
- `joined`
- `ready`
- `active`
- `eliminated`
- `left`
- `timed_out`

### Связи
- много `Participant` -> 1 `Match`
- 1 `Participant` -> много контролируемых `Entity`
- 1 `Participant` -> много `Command`

---

## 3.4. Context

### Смысл
Игровой контекст — самостоятельный слой игры со своими правилами времени, видимости, командами и сущностями.

### Примеры
- стратегическая карта;
- локальная локация;
- тактический бой;
- база;
- подземелье;
- фаза дипломатии.

### Ответственность
- быть контейнером состояния и сущностей;
- задавать локальные turn/time policies;
- определять активных участников;
- обеспечивать переходы в дочерние контексты;
- предоставлять контекстную проекцию UI.

### Обязательные поля
- `context_id`
- `match_id`
- `parent_context_id` nullable
- `context_type`
- `status`
- `state_version`
- `state_blob`
- `turn_policy_id`
- `time_policy_id`
- `visibility_policy_id`
- `generator_meta`
- `created_at`

### Дополнительные поля
- `label`
- `depth`
- `sequence_in_parent`
- `resolution_state`
- `started_at`
- `finished_at`
- `result_blob`

### Статусы
- `created`
- `initializing`
- `active`
- `resolving`
- `suspended`
- `finished`
- `failed`
- `archived`

### Жизненный цикл
1. Создаётся вручную или через transition.
2. Инициализируется генератором/проекцией.
3. Активируется.
4. Получает команды и события.
5. Может порождать дочерние контексты.
6. Завершается и отдаёт результат родителю.

### Связи
- много `Context` -> 1 `Match`
- 1 `Context` -> много `Entity`
- 1 `Context` -> много `Command`
- 1 `Context` -> много `Event`
- 1 `Context` -> много дочерних `Context`

---

## 3.5. ContextDefinition

### Смысл
Шаблон типа контекста внутри игры.

### Ответственность
- задавать тип контекста;
- определять доступные actions;
- указывать политики времени/видимости;
- указывать генератор и UI.

### Поля
- `context_type`
- `title`
- `description`
- `allowed_actions`
- `default_turn_policy`
- `default_visibility_policy`
- `generator_id`
- `ui_schema_id`
- `allowed_child_contexts`

---

## 3.6. Entity

### Смысл
Игровой объект внутри контекста.

### Примеры
- юнит;
- персонаж;
- группа;
- предмет;
- строение;
- сектор;
- тайник;
- аномалия;
- ресурсный узел.

### Ответственность
- хранить игровое состояние объекта;
- участвовать в разрешении правил;
- иметь владельца/контроллера;
- быть видимой или скрытой для разных сторон.

### Обязательные поля
- `entity_id`
- `match_id`
- `context_id`
- `archetype_id`
- `owner_participant_id` nullable
- `controller_participant_id` nullable
- `alive`
- `state_version`
- `components`
- `tags`
- `created_at`

### Дополнительные поля
- `display_name`
- `visibility_scope`
- `spawn_source`
- `parent_entity_id`
- `meta`

### Жизненный цикл
1. Создание генератором или событием.
2. Изменение компонент через события.
3. Возможно развёртывание в дочернем контексте.
4. Уничтожение, деактивация или архивирование.

### Связи
- много `Entity` -> 1 `Context`
- много `Entity` -> 1 `EntityArchetype`
- 1 `Entity` может ссылаться на родительскую `Entity`

---

## 3.7. EntityArchetype

### Смысл
Описание типа сущности и допустимого набора компонентов.

### Ответственность
- зафиксировать разрешённые компоненты;
- определить обязательные компоненты;
- описать default values;
- задать UI hints.

### Поля
- `archetype_id`
- `game_id`
- `name`
- `description`
- `required_components`
- `optional_components`
- `default_components`
- `tags`
- `ui_hints`

---

## 3.8. Component

### Смысл
Модульная часть состояния сущности.

### Примеры компонентов
- `position`
- `stats`
- `inventory`
- `vision`
- `owner`
- `ai`
- `status_effects`
- `movement`
- `combat`
- `resources`

### Ответственность
- хранить один аспект состояния;
- валидироваться по схеме;
- участвовать в diff/update логике.

### Общие поля компонента
- `component_type`
- `schema_version`
- `payload`

### Требования
- должен быть сериализуемым;
- должен быть валидируемым;
- должен поддерживать deterministic update.

---

## 3.9. ActionDefinition

### Смысл
Описание действия, доступного игроку/боту в UI и rule layer.

### Ответственность
- определить тип команды;
- описать payload schema;
- задать требования видимости и права;
- сообщить UI, как рендерить action.

### Поля
- `action_type`
- `title`
- `description`
- `command_schema`
- `availability_rules`
- `ui_control`
- `targeting_mode`

---

## 3.10. Command

### Смысл
Команда — сериализуемое намерение актора изменить мир.

### Ответственность
- быть входом в pipeline;
- быть воспроизводимой;
- быть идемпотентной;
- иметь ссылку на актор, контекст и match.

### Обязательные поля
- `command_id`
- `match_id`
- `context_id`
- `participant_id`
- `command_type`
- `payload`
- `client_request_id`
- `created_at`
- `status`

### Дополнительные поля
- `submitted_via`
- `expected_context_version`
- `causation_ui_action`
- `debug_meta`

### Статусы
- `received`
- `validated`
- `rejected`
- `accepted`
- `resolved`
- `failed`
- `cancelled`

### Жизненный цикл
1. Принимается API.
2. Валидация схемы.
3. Проверка прав и правил.
4. Либо reject, либо accept.
5. При resolve порождает события.
6. Маркируется итоговым статусом.

### Связи
- много `Command` -> 1 `Participant`
- много `Command` -> 1 `Context`
- 1 `Command` -> 0..N `Event`

---

## 3.11. CommandResult

### Смысл
Результат обработки команды.

### Поля
- `command_id`
- `success`
- `error_code` nullable
- `error_message` nullable
- `events_emitted`
- `new_projection_version`
- `resolved_at`

---

## 3.12. Event

### Смысл
Событие — подтверждённый факт изменения мира.

### Примеры
- `GroupMoved`
- `EnemyDetected`
- `BattleStarted`
- `DamageApplied`
- `EntityDestroyed`
- `TurnOpened`
- `TurnClosed`
- `ContextCreated`
- `ContextFinished`

### Ответственность
- быть частью журнала истины;
- быть воспроизводимым;
- иметь порядок в рамках context/match;
- иметь связь с causation command.

### Обязательные поля
- `event_id`
- `match_id`
- `context_id`
- `sequence_no`
- `event_type`
- `payload`
- `causation_command_id` nullable
- `created_at`

### Дополнительные поля
- `correlation_id`
- `visibility_scope`
- `aggregate_version`
- `producer`
- `tags`

### Жизненный цикл
1. Создаётся rule resolver.
2. Записывается в event store.
3. Применяется к projections.
4. Может публиковаться в realtime feed.
5. Используется для replay.

---

## 3.13. TurnState

### Смысл
Текущее состояние хода в контексте.

### Ответственность
- определить активную сторону;
- хранить дедлайн;
- задавать фазу и номер хода;
- привязывать fallback policy.

### Поля
- `turn_id`
- `context_id`
- `turn_number`
- `phase`
- `active_side_id`
- `deadline_at`
- `opened_at`
- `closed_at`
- `fallback_policy_id`
- `resolution_mode`

### Режимы
- `strict`
- `simultaneous`
- `wego`
- `hybrid`

### Фазы
- `opening`
- `collecting`
- `resolving`
- `closed`

---

## 3.14. TurnPolicy

### Смысл
Правило организации времени и очередности внутри контекста.

### Поля
- `turn_policy_id`
- `mode`
- `deadline_seconds`
- `auto_advance`
- `require_all_players_ready`
- `fallback_on_timeout`
- `resolution_order`

---

## 3.15. FallbackPolicy

### Смысл
Правило поведения, если игрок не походил вовремя.

### Варианты
- `skip_turn`
- `end_turn`
- `defensive_bot`
- `repeat_last_doctrine`
- `full_ai_control`

### Поля
- `fallback_policy_id`
- `strategy`
- `bot_policy_id` nullable
- `config`

---

## 3.16. Projection

### Смысл
Материализованное читаемое представление состояния.

### Типы
- summary projection;
- context projection;
- player-visible projection;
- UI projection;
- event feed projection.

### Ответственность
- дать быстрый доступ к состоянию;
- учитывать visibility;
- быть удобной для фронтенда;
- иметь версию.

### Поля
- `projection_id`
- `projection_type`
- `match_id`
- `context_id` nullable
- `participant_id` nullable
- `source_event_sequence`
- `version`
- `payload`
- `generated_at`

---

## 3.17. UISchema

### Смысл
Описание того, как клиент должен рендерить контекст.

### Ответственность
- задать layout;
- описать виджеты;
- задать map renderer;
- связать actions и panels;
- минимизировать custom frontend code.

### Поля
- `ui_schema_id`
- `context_type`
- `layout`
- `widgets`
- `map_renderer`
- `panels`
- `tables`
- `detail_cards`
- `action_bar`
- `timeline_widget`

---

## 3.18. GeneratorDefinition

### Смысл
Контракт генератора мира/контекста/encounter-а.

### Поля
- `generator_id`
- `scope`
- `version`
- `input_schema`
- `output_contract`
- `deterministic`
- `lazy_supported`

---

## 3.19. BotPolicy

### Смысл
Контракт поведения AI.

### Ответственность
- принимать доступную проекцию мира;
- выбирать команду;
- действовать в рамках тех же правил, что и игрок.

### Поля
- `bot_policy_id`
- `game_id`
- `context_type`
- `decision_mode`
- `input_projection_type`
- `config`
- `safety_limits`

---

## 3.20. Snapshot

### Смысл
Контрольная точка состояния для ускоренного восстановления.

### Поля
- `snapshot_id`
- `match_id`
- `context_id` nullable
- `event_sequence_up_to`
- `payload`
- `created_at`

---

## 3.21. Notification

### Смысл
Сообщение игроку о значимом игровом событии.

### Поля
- `notification_id`
- `user_id`
- `match_id`
- `context_id` nullable
- `kind`
- `title`
- `body`
- `is_read`
- `created_at`

---

## 4. Инварианты модели

Ниже — правила, которые должны быть истинны всегда.

### Матч
- у матча всегда не более одного root context;
- матч не может перейти в `active`, пока root context не инициализирован;
- finished match не принимает новые gameplay-команды.

### Контекст
- context принадлежит ровно одному match;
- parent_context_id, если задан, должен ссылаться на context того же match;
- archived context не должен принимать команды.

### Сущность
- entity принадлежит ровно одному context;
- entity archetype должен существовать в game definition;
- компоненты entity должны проходить валидацию по archetype contract.

### Команда
- command должна ссылаться на существующий match/context/participant;
- command должна быть идемпотентной по client_request_id;
- rejected command не создаёт событий.

### Событие
- sequence_no монотонно возрастает в рамках выбранного scope;
- event должен быть сериализуемым и воспроизводимым;
- event не должен нарушать visibility policy без redaction.

### Проекция
- projection version не может уменьшаться;
- projection должна указывать, до какого sequence_no она актуальна.

---

## 5. Агрегаты и границы транзакций

Практически полезно мыслить агрегатами.

### Агрегат `Match`
Включает:
- match metadata
- participants
- root state flags

### Агрегат `Context`
Включает:
- context state
- turn state
- active entities within transaction scope
- context events

### Агрегат `Entity`
Включает:
- entity meta
- components
- version

### Главная рекомендация
Основная транзакционная граница — **Context**.  
Именно внутри него обычно должны разрешаться команды.

---

## 6. Публичные интерфейсы ядра

Ниже — минимальный набор интерфейсов, который нужен в коде.

## 6.1. `GameRegistry`
Отвечает за:
- регистрацию игр;
- поиск `GameDefinition`;
- получение registries сущностей, команд, UI и генераторов.

## 6.2. `CommandHandler`
Отвечает за:
- принять команду;
- провести validation;
- вызвать resolver;
- записать events;
- обновить projections.

## 6.3. `RuleResolver`
Отвечает за:
- применить игровые правила;
- вернуть набор событий или ошибку.

## 6.4. `ProjectionBuilder`
Отвечает за:
- пересборку проекций для match/context/player.

## 6.5. `VisibilityResolver`
Отвечает за:
- редактирование истинного состояния в player-visible view.

## 6.6. `TransitionService`
Отвечает за:
- создание дочерних контекстов;
- перенос/проекцию сущностей;
- агрегацию результатов.

## 6.7. `TurnEngine`
Отвечает за:
- opening/closing turns;
- дедлайны;
- fallback logic.

## 6.8. `GeneratorRuntime`
Отвечает за:
- запуск генераторов;
- передачу seed;
- version pinning.

## 6.9. `BotRuntime`
Отвечает за:
- формирование bot input;
- запуск bot policy;
- отправку команд через тот же pipeline.

---

## 7. Минимальный набор enum-ов

Нужно завести заранее:

- `MatchStatus`
- `ParticipantStatus`
- `ContextStatus`
- `CommandStatus`
- `TurnPhase`
- `TurnMode`
- `ProjectionType`
- `NotificationKind`
- `VisibilityLevel`
- `ActorKind`

---

## 8. Минимальный набор ID типов

Лучше сразу сделать отдельные strong typedef/newtypes:

- `GameId`
- `MatchId`
- `ParticipantId`
- `ContextId`
- `EntityId`
- `CommandId`
- `EventId`
- `ProjectionId`
- `SnapshotId`
- `UserId`

---

## 9. Что нужно реализовать первым

Самый ранний core backlog по этому документу:

1. `GameDefinition`
2. `Match`
3. `Participant`
4. `Context`
5. `Entity`
6. `Command`
7. `Event`
8. `Projection`
9. `TurnState`
10. `UISchema`

И только после этого:
- `GeneratorDefinition`
- `BotPolicy`
- `Snapshot`
- `Notification`

---

## 10. Итог

Если сформулировать суть документа одной фразой:

> Платформа — это движок матчей, контекстов, сущностей, команд, событий и проекций, а конкретная игра — это пакет правил, схем и генераторов, которые подключаются к этому ядру.

Именно эта модель должна стать основой всех последующих документов и модулей кода.
