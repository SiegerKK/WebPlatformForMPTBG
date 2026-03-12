# Development Roadmap платформы

## 1. Назначение документа

Этот документ превращает архитектуру в последовательность исполнимых этапов.  
Его задача — дать:

- порядок разработки;
- milestones;
- definition of done;
- основу для GitHub issues / projects / epics.

---

## 2. Главный принцип roadmap

Нельзя начинать сразу с “полной платформы для всех игр”.

Правильный путь:

1. построить core;
2. проверить core на одной простой игре;
3. проверить абстракции на второй отличающейся игре;
4. только потом стабилизировать SDK.

---

## 3. Горизонт планирования

Я предлагаю делить развитие на 4 уровня:

- **Phase 0** — foundation
- **Phase 1** — platform MVP
- **Phase 2** — first playable demo
- **Phase 3** — hardening and externalization

---

## 4. Phase 0 — Foundation

## Цель
Поднять репозиторий и базовую инфраструктуру разработки.

## Результат
Есть runnable skeleton проекта.

## Задачи
- создать monorepo;
- разложить базовые директории;
- настроить Python tooling;
- настроить frontend tooling;
- добавить docker-compose;
- поднять Postgres, Redis, Temporal dev;
- сделать Makefile;
- создать README и onboarding;
- подключить basic CI.

## Definition of Done
- `make up` запускает базовые сервисы;
- `make test` работает;
- `apps/api` стартует;
- `apps/worker` стартует;
- `apps/web` стартует;
- есть health check endpoint;
- репозиторий пригоден для PR flow.

---

## 5. Phase 1 — Core Domain

## Цель
Реализовать минимальную метамодель платформы.

## Результат
Есть стабильные доменные сущности и contracts.

## Задачи
- реализовать `GameDefinition`;
- реализовать `Match`;
- реализовать `Participant`;
- реализовать `Context`;
- реализовать `Entity`;
- реализовать `Command`;
- реализовать `Event`;
- реализовать `Projection`;
- реализовать `TurnState`;
- описать enums и ID types;
- сделать registry для игр.

## Definition of Done
- сущности существуют в коде;
- есть валидируемые схемы;
- есть unit tests на создание и базовые инварианты;
- есть документированные контракты.

---

## 6. Phase 2 — Persistence Layer

## Цель
Сделать надёжное хранение состояния.

## Результат
Доменные объекты читаются и пишутся через repositories.

## Задачи
- таблицы users, matches, participants;
- таблицы contexts, entities;
- таблицы commands, events;
- таблицы projections, turn_states, snapshots;
- SQLAlchemy ORM models;
- repositories;
- transaction manager;
- Alembic migrations;
- optimistic locking version fields.

## Definition of Done
- можно создать match в БД;
- можно записать command и events;
- можно читать и обновлять entity;
- миграции проходят на чистой БД;
- есть integration tests с Postgres.

---

## 7. Phase 3 — Command Pipeline

## Цель
Сделать рабочий жизненный цикл команды.

## Результат
Команды принимаются, валидируются, порождают события и обновляют проекции.

## Задачи
- `CommandHandler`;
- schema validation;
- auth/permission hooks;
- context state validation;
- `RuleResolver` contract;
- event emission;
- projection update hooks;
- idempotency по client_request_id;
- error model.

## Definition of Done
- можно отправить команду в API;
- команда доходит до resolver;
- события пишутся;
- проекция обновляется;
- invalid command корректно reject-ится;
- есть integration tests на pipeline.

---

## 8. Phase 4 — Turn Engine and Timeouts

## Цель
Поддержать долгоживущие партии и таймеры ходов.

## Результат
Работает turn scheduling и fallback logic.

## Задачи
- `TurnEngine`;
- `TurnPolicy`;
- `FallbackPolicy`;
- `TurnWindowWorkflow`;
- дедлайны хода;
- auto-close window;
- timeout handling;
- bot fallback integration.

## Definition of Done
- контекст умеет открывать/закрывать ход;
- deadline хранится и соблюдается;
- по таймауту происходит fallback;
- всё покрыто replayable workflows.

---

## 9. Phase 5 — Projection and Visibility

## Цель
Сделать читаемые player-specific представления состояния.

## Результат
Фронтенд может получать UI-ready данные.

## Задачи
- `ProjectionBuilder`;
- `VisibilityResolver`;
- player-visible projection;
- context projection;
- match summary projection;
- event feed projection;
- projection rebuild path.

## Definition of Done
- игрок получает только свою проекцию;
- raw world state не светится наружу;
- проекции пересобираются из событий;
- есть тесты на fog/visibility.

---

## 10. Phase 6 — Context Transitions

## Цель
Поддержать многоуровневую игру.

## Результат
Родительский контекст умеет создавать дочерний и получать результат назад.

## Задачи
- `ContextFactory`;
- `TransitionService`;
- child context creation;
- entity projection/expansion;
- result aggregation;
- parent-child lifecycle integration.

## Definition of Done
- из одного context можно создать другой;
- дочерний context получает свои сущности;
- результат дочернего context влияет на родителя;
- это покрыто integration tests.

---

## 11. Phase 7 — SDK for Games

## Цель
Сделать подключение игр как пакетов, а не форков ядра.

## Результат
Появляется platform API для game authors.

## Задачи
- registry context definitions;
- registry entity archetypes;
- registry action definitions;
- registry generators;
- registry UI schemas;
- registry bots;
- docs для `GameDefinition`;
- шаблон game package.

## Definition of Done
- новая игра подключается как пакет;
- ядро не требует изменений для регистрации игры;
- есть working example package.

---

## 12. Phase 8 — Demo Sector backend

## Цель
Сделать первую демонстрационную игру на backend.

## Результат
Есть рабочая game package с правилами и генерацией.

## Задачи
- strategic map generator;
- tactical map generator;
- archetype-ы групп/юнитов;
- action definitions;
- strategic rules;
- tactical rules;
- transition rule `battle_trigger`;
- result aggregation;
- scripted bots.

## Definition of Done
- match Demo Sector можно создать;
- стратегический ход работает;
- бой создаётся автоматически;
- бой завершается;
- результат возвращается в strategy;
- replay детерминирован.

---

## 13. Phase 9 — Web Client MVP

## Цель
Сделать минимально играбельный браузерный клиент.

## Результат
Игрок может реально сыграть демо-партию.

## Задачи
- auth shell;
- lobby screens;
- match list;
- strategy screen;
- tactical screen;
- event log;
- turn timer;
- action dispatch;
- realtime updates;
- UI schema renderer.

## Definition of Done
- два игрока могут открыть матч;
- можно сделать стратегический ход;
- можно сыграть тактический бой;
- UI обновляется по событиям;
- fallback состояние видно в интерфейсе.

---

## 14. Phase 10 — Quality and Operations

## Цель
Подготовить систему к устойчивой разработке и тестированию.

## Результат
Есть CI/CD, наблюдаемость и надежный dev loop.

## Задачи
- structured logging;
- tracing;
- metrics;
- replay tests;
- e2e tests;
- GitHub Actions pipelines;
- staging environment;
- Terraform base deploy.

## Definition of Done
- PR запускает checks;
- staging разворачивается автоматически;
- replay и e2e тесты проходят;
- базовые runtime метрики видны.

---

## 15. Phase 11 — Documentation and Onboarding

## Цель
Сделать платформу понятной для будущих разработчиков игр.

## Результат
Появляется documentation-first DX.

## Задачи
- architecture docs;
- domain docs;
- sdk guide;
- game package guide;
- Demo Sector spec;
- runbooks;
- ADR log;
- issue templates.

## Definition of Done
- новый разработчик может поднять проект локально;
- новый разработчик может понять, как добавить archetype/action/context;
- есть пошаговый onboarding.

---

## 16. Приоритеты первого реального спринта

Если нужно начать прямо сейчас, первый спринт должен включать только:

1. monorepo skeleton;
2. docker-compose;
3. api hello world + health;
4. worker hello world;
5. web shell;
6. Postgres/Redis/Temporal local setup;
7. core domain models;
8. README + make commands.

Ничего сверх этого.

---

## 17. Приоритеты второго спринта

1. persistence tables;
2. repositories;
3. command model;
4. event model;
5. projection model;
6. first command pipeline;
7. create match endpoint;
8. seed demo match script.

---

## 18. Приоритеты третьего спринта

1. turn workflow;
2. timeout fallback;
3. root context generation;
4. player-visible projection;
5. strategy UI prototype.

---

## 19. Приоритеты четвёртого спринта

1. battle trigger transition;
2. tactical context;
3. tactical rules;
4. aggregation back to strategy;
5. basic bot.

---

## 20. Что не делать слишком рано

Не делать рано:
- marketplace модов;
- RL/ML ботов;
- очень гибкий visual game editor;
- рейтинговые системы;
- сложную аналитику;
- микросервисы;
- Kubernetes;
- mobile apps.

---

## 21. Риски

## 21.1. Архитектурный риск
Сделать слишком абстрактное ядро, которое невозможно реализовать быстро.

### Контрмера
Всё проверять на Demo Sector.

## 21.2. Инфраструктурный риск
Перегрузить проект DevOps-слоем раньше времени.

### Контрмера
Начать с docker-compose + ECS Fargate.

## 21.3. Продуктовый риск
Пытаться делать “сразу Warhammer/Stalker-class game”.

### Контрмера
Сначала демо-полигон.

## 21.4. DX-риск
Слишком слабая документация для Copilot и новых разработчиков.

### Контрмера
Документы писать параллельно с архитектурой.

---

## 22. Milestones

### M1 — Repo Foundation
Локально всё запускается.

### M2 — Domain Core
Есть основные сущности и contracts.

### M3 — Command/Event Core
Работает pipeline команды.

### M4 — Turn Engine
Работают дедлайны и автоходы.

### M5 — Multi-Context Core
Работают дочерние контексты.

### M6 — Demo Sector Backend
Игра playable на backend.

### M7 — Demo Sector Web
Игра playable в браузере.

### M8 — Cloud Staging
Есть staging deployment.

---

## 23. Definition of Success для первой фазы проекта

Проект можно считать успешным на первой большой фазе, если:

1. существует одна реально играбельная демо-игра;
2. она поддерживает асинхронный ход;
3. она поддерживает bot fallback;
4. она включает минимум два уровня контекста;
5. её можно развернуть в облаке;
6. её архитектура документирована;
7. по ней можно начинать делать вторую игру без переписывания ядра.

---

## 24. Итог

Если коротко:

> Сначала нужно доказать жизнеспособность ядра на одной маленькой игре, и только потом расширять платформу.

Этот roadmap специально построен так, чтобы:
- уменьшить риск;
- дать понятный порядок задач;
- не потерять темп разработки;
- быстро получить проверяемый результат.
