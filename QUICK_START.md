# Быстрый старт на Ubuntu — WebPlatformForMPTBG

## Требования

| Что | Минимум |
|-----|---------|
| ОС  | Ubuntu 20.04 / 22.04 / 24.04 (или любой Debian-based) |
| RAM | 2 GB |
| Диск | 5 GB свободного места |
| Интернет | Нужен при первом запуске (скачать Docker, образы) |

Docker Engine устанавливается **автоматически** скриптом, если его ещё нет.

---

## Один клик — запустить всё

```bash
# 1. Клонируй репозиторий (если ещё не сделал)
git clone https://github.com/SiegerKK/WebPlatformForMPTBG.git
cd WebPlatformForMPTBG

# 2. Запусти установку одной командой
bash setup.sh
```

Скрипт сам:
1. Проверит наличие Docker, при необходимости — установит его.
2. Создаст файл `.env` из `.env.example` с автосгенерированным `SECRET_KEY`.
3. Соберёт Docker-образы (≈3–5 минут при первом запуске).
4. Запустит все сервисы в фоне.
5. Подождёт, пока бэкенд станет доступен.
6. Выведет URL-адреса.

После завершения открой браузер:

| Адрес | Что |
|-------|-----|
| <http://localhost:3000> | Фронтенд (React) |
| <http://localhost:8000/docs> | Swagger API-документация |
| <http://localhost:8000/redoc> | ReDoc API-документация |

---

## Управление

```bash
# Остановить всё
make down
# или
docker compose down

# Запустить снова (без пересборки)
make up

# Посмотреть логи всех сервисов в реальном времени
make logs

# Посмотреть статус контейнеров
make status

# Перезапустить все сервисы
make restart

# Пересобрать образы (после изменений в коде)
make build && make up
```

---

## Разработка без Docker (bare-metal)

Если хочешь запускать бэкенд/фронтенд напрямую:

```bash
# Postgres и Redis всё равно нужны — запусти только их через Docker:
docker compose up -d db redis

# Установи зависимости бэкенда
cd backend
pip install -r requirements.txt

# Примени миграции
alembic upgrade head

# Запусти бэкенд с hot-reload
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# В отдельном терминале — фронтенд с hot-reload
cd frontend
npm install
npm run dev        # открывается на http://localhost:3000
```

---

## Первое обновление кода

```bash
git pull
make build   # пересобрать образы
make restart # перезапустить контейнеры
```

---

## Структура сервисов

```
docker compose ps    →  показывает:

NAME        STATUS    PORTS
db          healthy   5432/tcp
redis       healthy   6379/tcp
backend     healthy   0.0.0.0:8000->8000/tcp
worker      running   (фоновые задачи)
frontend    running   0.0.0.0:3000->80/tcp
```

---

## Часто задаваемые вопросы

### `permission denied while trying to connect to the Docker API`

Это значит, что текущий пользователь ещё не входит в группу `docker`  
(или сессия не обновилась после добавления в группу).

**Вариант 1 — скрипт сам справится (рекомендуется):**  
`setup.sh` автоматически обнаруживает эту проблему и использует `sudo docker`  
на время текущего запуска. После завершения скрипта платформа будет работать.  
Чтобы следующие запуски тоже работали без `sudo`, выполни шаги ниже.

**Вариант 2 — постоянное решение (без sudo в будущем):**

```bash
sudo usermod -aG docker $USER   # добавить пользователя в группу
# Выйди из системы и зайди снова, затем повтори:
bash setup.sh
```

**Вариант 3 — быстрый способ без перелогина (только для текущего терминала):**

```bash
newgrp docker   # активировать группу в текущей сессии
bash setup.sh   # запустить в той же оболочке
```

### Порт 3000 или 8000 занят

Измени порт в `docker-compose.yml`:
```yaml
ports:
  - "8080:8000"   # вместо 8000 будет 8080
```

### Посмотреть логи конкретного сервиса

```bash
docker compose logs -f backend
docker compose logs -f frontend
docker compose logs -f db
```

### Полная очистка (удалить данные БД)

```bash
docker compose down -v   # -v удаляет volume с данными PostgreSQL
```

---

## Переменные окружения (`.env`)

После первого запуска `setup.sh` в корне репозитория появится файл `.env`.  
Основные параметры:

| Переменная | По умолчанию | Описание |
|------------|-------------|----------|
| `SECRET_KEY` | *(авто)* | Секрет для JWT. **Смени в продакшне!** |
| `DATABASE_URL` | `postgresql://...@db/mptbg` | Строка подключения к БД |
| `REDIS_URL` | `redis://redis:6379` | Адрес Redis |
| `CORS_ORIGINS` | `http://localhost:3000` | Разрешённые CORS-источники |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Время жизни JWT-токена |
