# Repository Layout и модульная структура monorepo

## 1. Назначение документа

Этот документ описывает рекомендуемую структуру монорепозитория проекта.  
Его задача — сделать репозиторий:

- понятным для человека;
- удобным для Copilot;
- пригодным для независимой разработки backend/frontend/game packages;
- устойчивым к росту числа игр.

---

## 2. Главный принцип

Монорепозиторий должен отражать архитектуру проекта:

- приложения в `apps/`;
- платформенные библиотеки в `packages/`;
- инфраструктура в `infra/`;
- документация в `docs/`;
- интеграционные и e2e тесты в `tests/`.

---

## 3. Корневой layout

```text
repo/
├─ apps/
├─ packages/
├─ infra/
├─ docs/
├─ tests/
├─ scripts/
├─ .github/
├─ Makefile
├─ docker-compose.yml
├─ README.md
└─ pyproject.toml / package manifests
```

---

## 4. Каталог `apps/`

## 4.1. `apps/api/`
Основное FastAPI-приложение.

Что лежит внутри:
- `main.py`
- `config/`
- `routers/`
- `dependencies/`
- `middleware/`
- `auth/`
- `db/`
- `adapters/`
- `ws/`
- `bootstrap/`

Роль:
- принимать HTTP/WS запросы;
- отдавать проекции;
- принимать команды;
- запускать сервисы ядра.

## 4.2. `apps/worker/`
Фоновые worker-процессы.

Что лежит внутри:
- `main.py`
- `temporal_workflows/`
- `temporal_activities/`
- `bot_tasks/`
- `projection_tasks/`
- `snapshot_tasks/`

Роль:
- исполнять durable workflows;
- выполнять тяжёлые операции;
- запускать ботов;
- пересобирать проекции.

## 4.3. `apps/web/`
Next.js клиент.

Что лежит внутри:
- `app/`
- `components/`
- `features/`
- `lib/`
- `hooks/`
- `store/`
- `styles/`

Роль:
- лобби;
- экраны матча и контекста;
- UI-рендеринг схем;
- подписка на realtime updates.

## 4.4. `apps/admin/` (опционально)
Внутренний интерфейс для администрирования:
- debug tools;
- replay viewer;
- projection rebuild UI;
- match inspection.

---

## 5. Каталог `packages/`

Это сердце системы.

## 5.1. `packages/core/`
Чистое доменное ядро без HTTP и UI.

Подкаталоги:
- `models/`
- `types/`
- `enums/`
- `contracts/`
- `errors/`
- `value_objects/`

Там не должно быть:
- FastAPI
- SQLAlchemy ORM-specific кода
- React
- внешней инфраструктурной логики

## 5.2. `packages/engine/`
Исполнительный слой платформы.

Подкаталоги:
- `command_pipeline/`
- `resolver/`
- `projection/`
- `turns/`
- `transitions/`
- `visibility/`
- `replay/`
- `services/`

Роль:
- реализовать логику исполнения поверх core contracts.

## 5.3. `packages/persistence/`
Слой хранения данных.

Подкаталоги:
- `repositories/`
- `orm_models/`
- `mappers/`
- `transactions/`
- `migrations_support/`

Роль:
- изолировать SQLAlchemy/DB-детали от доменного ядра.

## 5.4. `packages/sdk/`
SDK для авторов игр.

Подкаталоги:
- `game_definition/`
- `entity_registry/`
- `action_registry/`
- `context_registry/`
- `generator_api/`
- `bot_api/`
- `ui_api/`

Роль:
- предоставить удобный API подключения игр.

## 5.5. `packages/schemas/`
Схемы контрактов и shared-типизация.

Что лежит:
- Pydantic-модели API;
- JSON Schema;
- общие DTO;
- Zod-compatible exports при необходимости.

## 5.6. `packages/ui-contracts/`
Схемы для frontend rendering слоя.

Что лежит:
- `UISchema` types
- widget contracts
- map renderer contracts
- action panel schemas

## 5.7. `packages/bots/`
Общие базовые реализации ботов.

Подкаталоги:
- `runtime/`
- `policies/`
- `utilities/`
- `fallbacks/`

## 5.8. `packages/games/`
Каталог игр.

Структура:
```text
packages/games/
├─ demo_sector/
├─ stalker_zone_prototype/
└─ ...
```

Каждая игра должна быть изолирована и не ломать ядро.

## 5.9. `packages/shared/`
Только действительно общие вещи:
- time helpers
- id generation
- tracing helpers
- common serialization

Нельзя превращать `shared/` в мусорную корзину.

---

## 6. Рекомендуемая структура `packages/games/demo_sector/`

```text
packages/games/demo_sector/
├─ definition.py
├─ metadata/
├─ contexts/
├─ entities/
├─ actions/
├─ rules/
├─ generators/
├─ ui/
├─ bots/
├─ balance/
├─ fixtures/
└─ tests/
```

### Назначение
- `definition.py` — точка регистрации игры
- `contexts/` — типы контекстов
- `entities/` — archetype-ы
- `actions/` — команды/действия
- `rules/` — игровые правила
- `generators/` — генерация карты и боёв
- `ui/` — UI-схемы
- `bots/` — scripted AI
- `balance/` — числовые таблицы
- `fixtures/` — demo-данные
- `tests/` — game-level тесты

---

## 7. Каталог `infra/`

## 7.1. `infra/terraform/`
Инфраструктура как код.

Подкаталоги:
- `modules/`
- `environments/dev/`
- `environments/staging/`
- `environments/prod/`

## 7.2. `infra/docker/`
Dockerfile-ы и container helpers.

Например:
- `api.Dockerfile`
- `worker.Dockerfile`
- `web.Dockerfile`

## 7.3. `infra/temporal/`
Конфиги и локальные утилиты Temporal.

## 7.4. `infra/github-actions/`
Шаблоны workflows и composite actions.

---

## 8. Каталог `docs/`

Рекомендуемая структура:

```text
docs/
├─ architecture/
├─ adr/
├─ sdk/
├─ games/
├─ api/
├─ runbooks/
└─ onboarding/
```

### `docs/architecture/`
- общий blueprint
- domain model
- repository layout
- workflows
- deployment

### `docs/adr/`
Architecture Decision Records.

### `docs/sdk/`
Руководства по созданию игр.

### `docs/games/`
Спецификации конкретных игр.

### `docs/api/`
API contracts и примеры.

### `docs/runbooks/`
Operational docs:
- projection rebuild
- incident response
- bot fallback issues

### `docs/onboarding/`
Гайды для разработчиков.

---

## 9. Каталог `tests/`

Корневой `tests/` нужен для интеграционных и end-to-end сценариев, которые пересекают несколько приложений и пакетов.

Структура:
```text
tests/
├─ integration/
├─ contract/
├─ replay/
├─ e2e/
└─ fixtures/
```

### Назначение
- `integration/` — API + DB + worker
- `contract/` — API/UI schema compatibility
- `replay/` — deterministic tests
- `e2e/` — Playwright сценарии
- `fixtures/` — тестовые данные

---

## 10. Каталог `scripts/`

В `scripts/` должны жить утилиты, которые полезны разработчику, но не являются частью рантайма платформы.

Примеры:
- генерация demo match;
- импорт fixture-данных;
- migration helpers;
- projection repair utilities;
- replay export.

---

## 11. Рекомендации по именованию

### Python backend
- модули — `snake_case`
- классы — `PascalCase`
- интерфейсы/контракты — явные имена типа `GameDefinition`, `RuleResolver`

### Frontend
- компоненты — `PascalCase.tsx`
- hooks — `useSomething.ts`
- feature folders по бизнес-областям

### Документы
- `snake_case.md`
- без пробелов
- без “final_final_v2”

---

## 12. Что должно быть в корне репозитория

Минимальный набор:

- `README.md`
- `Makefile`
- `docker-compose.yml`
- `.editorconfig`
- `.gitignore`
- `.env.example`
- `pyproject.toml`
- frontend package manifests
- `pre-commit-config.yaml`

---

## 13. Какой код не должен лежать где

### Не класть в `apps/api/`
- доменные модели игры
- rule logic
- генераторы

### Не класть в `packages/core/`
- SQLAlchemy ORM entities
- FastAPI routers
- Redis adapters
- boto3 клиенты

### Не класть в `packages/games/*`
- общую инфраструктурную логику платформы

### Не класть в `shared/`
- всё подряд, что некуда положить

---

## 14. Границы ответственности между пакетами

### `core`
Что существует в домене.

### `engine`
Как домен исполняется.

### `persistence`
Как домен хранится.

### `sdk`
Как игры подключаются.

### `games/*`
Какие именно правила/контент у конкретной игры.

### `apps/*`
Как всё это поднимается как runnable приложения.

---

## 15. Рекомендуемый порядок создания директорий

Сначала создать:

1. `apps/api`
2. `apps/worker`
3. `apps/web`
4. `packages/core`
5. `packages/engine`
6. `packages/sdk`
7. `packages/games/demo_sector`
8. `packages/persistence`
9. `docs/architecture`
10. `tests/integration`

---

## 16. Минимальный стартовый набор файлов

### В backend
- `packages/core/models/match.py`
- `packages/core/models/context.py`
- `packages/core/models/entity.py`
- `packages/core/models/command.py`
- `packages/core/models/event.py`
- `packages/core/models/projection.py`

### В engine
- `packages/engine/command_pipeline/handler.py`
- `packages/engine/projection/builder.py`
- `packages/engine/turns/engine.py`
- `packages/engine/transitions/service.py`

### В sdk
- `packages/sdk/game_definition/base.py`
- `packages/sdk/registries.py`

### В game package
- `packages/games/demo_sector/definition.py`

### В apps
- `apps/api/main.py`
- `apps/worker/main.py`
- `apps/web/app/page.tsx`

---

## 17. Итог

Если коротко:

> Структура репозитория должна отражать архитектурные границы платформы, а не случайный порядок появления кода.

Это даст:
- устойчивость;
- хорошую навигацию;
- понятный контекст для Copilot;
- возможность расти от одной демо-игры к платформе для множества игр.
