# Zone Stalkers — оптимизация процесса тика и auto-run без специальных решений для пустой карты

## Назначение документа

Этот документ описывает общий план оптимизации процесса течения времени в `Zone Stalkers`.

Важно: это **не специальный fast-path для пустой карты**. Цель — улучшить архитектуру auto-tick/tick-loop так, чтобы ускорение работало корректно и эффективно для любого состояния мира:

- пустая карта;
- карта с 50 локациями;
- карта с NPC;
- карта с active events;
- карта с выбросами;
- debug-map;
- auto-run на `x10`, `x100`, `x600` и потенциально выше.

Основные вопросы:

```text
1. Можно ли избавиться от фиксированной паузы `await asyncio.sleep(0.1)` как от ограничителя скорости?
2. Можно ли иначе считать скорость auto-run?
3. Можно ли уменьшить нагрузку вне Brain v3?
```

Ответ: да. Для этого нужно перейти от схемы:

```text
sleep fixed interval
→ try one tick
→ sleep fixed interval again
```

к схеме:

```text
speed accumulator / catch-up scheduler
→ calculate how many game minutes are due
→ process due minutes efficiently
→ persist/broadcast at controlled cadence
```

---

# 1. Текущая проблема

Сейчас debug auto-ticker работает примерно так:

```python
while True:
    await asyncio.sleep(0.1)
    result = await asyncio.to_thread(tick_debug_auto_matches)
```

А внутри `tick_debug_auto_matches()` используется throttle:

```python
_TICK_INTERVALS = {
    "realtime": 60.0,
    "x10": 6.0,
    "x100": 0.6,
    "x600": 0.1,
}
```

То есть для `x600` система пытается сделать один игровой tick каждые `0.1s`.

Проблема в том, что реальный период получается не `0.1s`, а:

```text
0.1s sleep + время tick_debug_auto_matches + Redis/DB/WS/compress/decompress overhead
```

Если tick pipeline занимает 0.4–0.5 секунды, то фактическая скорость падает примерно до `x100`, даже если выбрано `x600`.

## Почему это архитектурно плохо

Фиксированная пауза превращается в нижнюю границу времени одного цикла:

```text
real_cycle_time >= sleep_time + work_time
```

Для `x600` это особенно заметно, потому что желаемый интервал уже очень маленький.

---

# 2. Новая модель скорости: accumulator вместо sleep-as-throttle

## 2.1. Главный принцип

Auto-run должен считать не “пора ли сделать один tick”, а:

```text
сколько игровых минут должно было пройти с прошлого обновления?
```

Для этого нужен speed accumulator.

```text
real_elapsed_seconds × speed_multiplier = game_elapsed_seconds
```

Так как 1 tick = 1 игровая минута:

```text
ticks_due = floor(accumulated_game_seconds / 60)
```

Пример для `x600`:

```text
1 real second × 600 = 600 game seconds = 10 game minutes = 10 ticks
```

Если loop проснулся через 0.25 секунды:

```text
0.25 × 600 = 150 game seconds = 2.5 game minutes
→ due 2 ticks
→ 0.5 game minutes остаются в accumulator
```

## 2.2. Скорость больше не должна быть равна фиксированной паузе

Вместо:

```python
interval = 0.1
if now - last_tick < interval:
    continue
run_one_tick()
```

нужно:

```python
elapsed_real = now - last_update
accumulated_game_seconds += elapsed_real * speed_multiplier

due_ticks = int(accumulated_game_seconds // 60)
accumulated_game_seconds -= due_ticks * 60

run_due_ticks(due_ticks)
```

## 2.3. Рекомендуемые multipliers

```python
AUTO_TICK_SPEED_MULTIPLIERS = {
    "realtime": 1,
    "x10": 10,
    "x100": 100,
    "x600": 600,
}
```

Это проще и правильнее, чем хранить интервалы.

---

# 3. Новый auto-ticker loop

## 3.1. Цель

Background loop должен быть частым, но лёгким.

Он не должен искусственно добавлять 100ms к каждому tick.

Вместо этого он должен:

1. просыпаться часто;
2. считать due ticks;
3. запускать обработку только если due ticks > 0;
4. не запускать несколько конкурентных обработчиков одного context;
5. уметь догонять отставание в разумных пределах.

## 3.2. Пример loop

```python
_DEBUG_AUTO_TICK_POLL_INTERVAL = 0.02  # 20ms, только lightweight polling
_MAX_TICKS_PER_BATCH = 30              # защита от runaway catch-up

async def _debug_auto_ticker() -> None:
    while True:
        await asyncio.sleep(_DEBUG_AUTO_TICK_POLL_INTERVAL)
        try:
            await asyncio.to_thread(tick_debug_auto_matches_accumulator)
        except Exception:
            logger.exception("Debug auto-ticker error")
```

Важно: `sleep(0.02)` теперь не является скоростью игры. Это просто polling interval.

Скорость считается через elapsed real time.

## 3.3. Per-context runtime state

Нужно хранить runtime-only состояние auto-tick:

```python
_auto_tick_runtime = {
    context_id: {
        "last_real_ts": 12345.67,
        "game_seconds_accum": 42.0,
        "running": False,
    }
}
```

Это не обязательно сохранять в state_blob. Это runtime scheduler state.

При рестарте сервера accumulator может сброситься — это нормально для debug auto-run.

---

# 4. Защита от конкурентных ticks

## 4.1. Проблема

Если auto-ticker вызывается часто, нельзя допустить, чтобы один и тот же match/context тиковался параллельно.

Нужен per-context lock / running flag.

## 4.2. Runtime lock

```python
_context_tick_locks: dict[str, threading.Lock] = {}
```

Или проще:

```python
_running_contexts: set[str] = set()
```

Pseudo-code:

```python
if ctx_id in _running_contexts:
    continue
_running_contexts.add(ctx_id)
try:
    process_context_auto_tick(ctx_id)
finally:
    _running_contexts.discard(ctx_id)
```

## 4.3. Acceptance criteria

```text
[ ] Один context не тикается параллельно сам с собой.
[ ] Если предыдущий batch ещё работает, следующий loop не запускает второй batch.
[ ] Отставание аккуратно накапливается или ограничивается.
```

---

# 5. Batch advancement вместо одного save на каждую минуту

## 5.1. Проблема

Сейчас каждый игровой tick проходит весь pipeline:

```text
load state
→ tick one minute
→ save state
→ commit
→ build delta
→ broadcast ws
```

Для `x600` это до 10 раз в секунду.

Но игроку и UI не обязательно получать 10 отдельных сохранений и 10 отдельных broadcast в секунду, особенно если auto-run идёт быстро.

## 5.2. Новая модель

Если accumulator насчитал `due_ticks = 10`, нужно не делать 10 полных `tick_match()` с load/save/commit/ws каждый раз, а делать:

```text
load state once
→ apply 10 world ticks in memory
→ save state once
→ commit once
→ emit one aggregated delta/ws update
```

## 5.3. Новый API ruleset

Добавить batch method:

```python
class ZoneStalkerRuleSet:
    def tick_many(self, match_id: str, db: Session, n: int) -> dict:
        ...
```

Или generic helper:

```python
def tick_match_many(match_id: str, db: Session, max_ticks: int) -> dict:
    ...
```

## 5.4. Внутренний алгоритм

```python
def tick_zone_map_many(state: dict, n: int) -> tuple[dict, list[dict]]:
    events = []
    for i in range(n):
        state, tick_events = tick_zone_map(state)
        events.extend(tick_events)

        if should_stop_batch(state, tick_events):
            break

    return state, events
```

Это уже даёт выигрыш, потому что load/save/commit/ws делаются один раз на batch, а не на каждый tick.

## 5.5. Дальнейшее улучшение

После внедрения event-driven scheduled actions часть batch можно будет сворачивать ещё сильнее, но первый MVP может просто вызывать `tick_zone_map` в цикле внутри одного loaded state.

Даже это сильно снижает overhead:

```text
Redis load/decompress: 1 раз вместо N
Redis save/compress: 1 раз вместо N
DB commit: 1 раз вместо N
WS broadcast: 1 раз вместо N
context lookup: 1 раз вместо N
```

## 5.6. Stop conditions внутри batch

Batch нельзя слепо прогонять, если произошло важное событие, на котором UI/игрок должен остановиться.

Останавливать batch при:

```text
- game_over;
- player decision required;
- human agent scheduled_action completed;
- emission_warning;
- emission_started;
- emission_ended;
- combat_started involving human/current viewed agent;
- active zone_event requires player choice;
- stop_on_decision=true для debug_advance_turns;
- serious error;
- max batch size reached.
```

Для обычного auto-run без stop_on_decision можно не останавливаться на каждом bot decision, иначе x600 будет постоянно тормозить.

## 5.7. Aggregated result

Response:

```json
{
  "ticks_advanced": 10,
  "world_turn": 12350,
  "world_day": 2,
  "world_hour": 14,
  "world_minute": 30,
  "events_emitted": 17,
  "new_events_preview": [...],
  "zone_delta": {...}
}
```

---

# 6. Не слать WS на каждую игровую минуту при быстрой скорости

## 6.1. Проблема

Даже compact delta становится overhead, если отправлять её на каждый minute tick.

Для `x600` нормальная частота UI-update должна быть ограничена:

```text
2–5 updates/sec
```

Не 10–60 updates/sec.

## 6.2. Broadcast cadence

Добавить:

```yaml
auto_tick:
  max_ws_updates_per_second: 4
```

Runtime:

```python
last_ws_sent_ts[context_id]
```

Если batch не содержит critical event, можно coalesce broadcast.

## 6.3. Critical events bypass

WS отправлять сразу при:

```text
- game_over;
- human agent death;
- player action completed;
- emission warning/start/end;
- combat involving player/current selected agent;
- resync required;
```

## 6.4. Normal events coalescing

Обычные bot events можно агрегировать:

```json
{
  "type": "zone_delta",
  "ticks_advanced": 8,
  "event_count": 24,
  "new_events_preview": [...last 10...]
}
```

---

# 7. Auto-tick flags не должны читаться из full state

## 7.1. Проблема

Сейчас `get_context_flag()` читает compressed full state из Redis, распаковывает JSON и достаёт одно поле.

Для auto-tick это происходит несколько раз на context:

```text
auto_tick_enabled
debug_auto_tick
auto_tick_speed
auto_tick_slow_mode
```

Это дорогой fixed overhead, не связанный с NPC Brain.

## 7.2. Решение

При `set_auto_tick` писать отдельные lightweight Redis keys:

```text
ctx:auto_tick:<context_id>:enabled
ctx:auto_tick:<context_id>:speed
ctx:auto_tick:<context_id>:updated_at
```

Или один JSON:

```text
ctx:auto_tick:<context_id>
```

Value:

```json
{
  "enabled": true,
  "speed": "x600",
  "updated_at": 123456.7
}
```

## 7.3. Fallback

Если key отсутствует:

```text
fallback to get_context_flag once
then populate lightweight key
```

## 7.4. When to update

В `set_auto_tick` command:

1. обновить state flags для совместимости;
2. обновить lightweight Redis key;
3. отправить WS `auto_tick_changed`.

## 7.5. Acceptance criteria

```text
[ ] tick_debug_auto_matches does not decompress full state just to read speed/enabled.
[ ] set_auto_tick updates lightweight runtime key.
[ ] Backward compatibility works if key is missing.
[ ] Auto-run survives old states.
```

---

# 8. Уменьшить load/save overhead вне Brain v3

## 8.1. Проблема

Каждый tick делает:

```text
Redis GET compressed state
→ zlib decompress
→ json.loads
→ world tick
→ json.dumps
→ zlib compress
→ Redis SET
```

Даже без NPC это дорого.

## 8.2. Batch save

Первый уровень решения — batch advancement:

```text
load once, save once per batch
```

## 8.3. Lower compression level for hot runtime state

Сейчас zlib compress level 6.

Для hot auto-tick можно рассмотреть:

```python
zlib.compress(..., level=1)
```

или конфиг:

```yaml
state_cache:
  compression_level: 1
```

Level 1 обычно существенно быстрее, а сжатие всё ещё хорошее.

Важно: это нужно померить profiler-ом.

## 8.4. Skip DB commit if nothing DB-persisted

`save_context_state()` может не писать DB, если Redis включён и persist interval не достигнут.

Но `ruleset.tick()` всё равно вызывает:

```python
db.commit()
```

Нужно проверить, есть ли dirty ORM objects. Если нет событий и state не flush-ится в DB, можно избежать лишнего commit.

Осторожно: если были GameEvent inserts, commit нужен.

Pseudo:

```python
state_db_written = save_context_state(...)
if emitted_events or state_db_written or match_status_changed or event_contexts_changed:
    db.commit()
else:
    db.rollback()  # or no-op close session
```

## 8.5. Event inserts batching

Если batch emits many events, sequence allocation and inserts should happen as one batch.

Current code already allocates sequence range for list of map events. Keep that pattern for batch events.

## 8.6. Event preview only

WS should receive only compact preview, full event log remains in DB.

For batch:

```text
preview = last 10 or most important 10
```

---

# 9. Оптимизация DB queries в ticker path

## 9.1. Проблема

`tick_match()` каждый раз делает:

```text
query Match
query GameContext zone_map
later query GameContext again for WS context_id
query active zone_event contexts
```

Для high-speed auto-run это заметно.

## 9.2. Context cache

Текущая ветка уже кеширует context_id → match_id для debug auto ticker.

Следующий шаг: cache match/context metadata для tick:

```python
@dataclass
class AutoTickContextRef:
    context_id: str
    match_id: str
    game_id: str
    match_status: str
```

## 9.3. Avoid duplicate zone_ctx lookup

В `ruleset.tick()` уже найден `zone_ctx`.

Не надо в `tick_match()` снова делать query для `context_id_str`.

В result из ruleset вернуть:

```json
{
  "context_id": "..."
}
```

Тогда ticker использует `result["context_id"]`.

## 9.4. Active zone events only if present

Если в state нет `active_events` или список пустой, не делать DB query active zone_event contexts.

Сейчас `ruleset.tick()` всегда делает query:

```python
event_ctxs = db.query(GameContext).filter(... context_type == "zone_event" ...).all()
```

Оптимизация:

```python
if new_state.get("active_events"):
    query active event contexts
else:
    skip
```

Это общий фикс, не только для пустой карты.

---

# 10. `deepcopy(state)` остаётся дорогим вне Brain v3

## 10.1. Проблема

`tick_zone_map()` начинает с:

```python
state = copy.deepcopy(state)
```

Это не Brain v3, но копирует весь мир.

## 10.2. Краткосрочное решение

В batch mode хотя бы делать `deepcopy` только внутри первого tick? Сейчас если `tick_zone_map_many` вызывает `tick_zone_map` N раз, каждый раз будет deepcopy.

Это плохо.

Для batch MVP лучше добавить внутренний параметр:

```python
def tick_zone_map(state: dict, *, copy_state: bool = True):
    if copy_state:
        state = copy.deepcopy(state)
```

Batch:

```python
state = copy.deepcopy(state)
for _ in range(n):
    state, evs = tick_zone_map(state, copy_state=False)
```

Так внутри batch будет один deepcopy вместо N.

## 10.3. Дальнейшее решение

Позже заменить full deepcopy на copy-on-write runtime, как описано в CPU optimization plan.

## 10.4. Acceptance criteria

```text
[ ] Single tick behavior unchanged.
[ ] Batch tick does one initial copy, not N deepcopies.
[ ] Input state is not mutated.
[ ] Tests cover tick_many with N > 1.
```

---

# 11. Batch delta вместо N отдельных delta

## 11.1. Проблема

Если сделать 10 ticks внутри batch, не нужно строить 10 deltas.

Нужно строить одну итоговую delta:

```text
old_state before batch
→ new_state after batch
```

## 11.2. Aggregated events

Для WS:

```text
event_count = total events in batch
new_events_preview = compact last N events
```

Для DB:

```text
all events inserted normally
```

## 11.3. Acceptance criteria

```text
[ ] Batch emits one zone_delta per broadcast cycle.
[ ] Delta base_revision is old revision before batch.
[ ] Delta revision is final revision after batch.
[ ] Frontend can apply it once.
```

---

# 12. Пересчёт `state_revision` при batch

## 12.1. Варианты

### Вариант A: revision +1 per batch

```text
state_revision += 1
```

Плюсы:

```text
меньше revision churn
проще delta
```

Минусы:

```text
revision больше не равен количеству игровых минут
```

### Вариант B: revision + ticks_advanced

```text
state_revision += ticks_advanced
```

Плюсы:

```text
revision отражает количество применённых ticks
```

Минусы:

```text
может быть непривычно, но это нормально
```

Рекомендация: использовать **revision +1 per saved state update**, а `world_turn` уже показывает игровое время.

То есть revision — это версия синхронизации state, а не номер тика.

---

# 13. Как считать скорость после изменения модели

## 13.1. Effective speed

Добавить метрику:

```text
effective_speed = game_minutes_advanced / real_minutes_elapsed
```

Или:

```python
effective_speed = ticks_advanced * 60 / real_elapsed_seconds
```

Если за 1 секунду advanced 10 ticks:

```text
10 game minutes / 1 real second = 600×
```

## 13.2. Metrics

В performance metrics добавить:

```text
auto_tick_speed_target
auto_tick_effective_speed
ticks_due
ticks_advanced
ticks_dropped_or_capped
batch_size
batch_total_ms
batch_tick_logic_ms
batch_load_state_ms
batch_save_state_ms
batch_db_ms
batch_ws_ms
accumulator_game_seconds
```

## 13.3. UI display

Debug UI должен показывать:

```text
Target: x600
Effective: x540
Batch: 8–10 ticks/update
Backend: 42ms/update
```

Это лучше, чем просто кнопка `x600`, которая фактически идёт как `x100` без объяснения.

---

# 14. Ограничение catch-up

## 14.1. Проблема

Если сервер подвис на 10 секунд при `x600`, accumulator насчитает:

```text
10 sec × 600 = 6000 game sec = 100 ticks
```

Нельзя всегда пытаться мгновенно догнать 100 ticks, иначе будет spike.

## 14.2. Cap

```yaml
auto_tick:
  max_ticks_per_batch: 30
  max_catchup_batches_per_loop: 1
```

Если due_ticks больше cap:

```text
process cap
keep remainder or drop some lag depending on mode
```

## 14.3. Modes

### Accurate mode

Сохранять remainder, пытаться догонять постепенно.

### Realtime smooth mode

Если lag слишком большой, сбрасывать часть accumulator.

Для debug auto-run лучше:

```text
cap due ticks, keep limited remainder, avoid runaway
```

Config:

```yaml
auto_tick:
  max_accumulated_ticks: 60
```

---

# 15. Не Brain v3: список оптимизаций по приоритету

## P0 — самые важные

```text
1. Accumulator speed model вместо sleep/throttle intervals.
2. Batch tick: load/save/commit/ws один раз на несколько игровых минут.
3. Auto_tick flags в lightweight Redis keys, без full state decompress.
4. Skip active zone_event DB query when active_events empty.
5. Avoid duplicate zone_map context lookup for WS context_id.
6. Batch mode: один deepcopy на batch, не один deepcopy на tick.
```

## P1 — важные

```text
7. Lower Redis compression level for hot state or make it configurable.
8. Skip db.commit when no ORM changes and no events.
9. Coalesce WS broadcasts by cadence.
10. Build one batch delta instead of per-tick delta.
11. Add effective speed metrics.
```

## P2 — следующие PR

```text
12. Copy-on-write state runtime.
13. Dirty-set delta.
14. Event-driven scheduled actions.
15. Lazy needs.
16. Static/runtime state split.
```

---

# 16. Suggested implementation plan

## Этап 1 — metrics first

Добавить metrics:

```text
target_speed
effective_speed
ticks_due
ticks_advanced
batch_size
batch_total_ms
load_state_ms
tick_logic_ms
save_state_ms
db_commit_ms
ws_ms
flag_read_ms
```

Это нужно, чтобы подтвердить проблему и видеть прирост.

## Этап 2 — lightweight auto_tick state

Изменить `set_auto_tick`:

```text
- оставить запись flags в state для совместимости;
- дополнительно писать lightweight Redis key.
```

Изменить `tick_debug_auto_matches`:

```text
- читать lightweight key;
- не вызывать get_context_flag 3–4 раза на context every loop.
```

## Этап 3 — accumulator scheduler

Заменить interval throttle на speed multiplier accumulator.

```text
x600 должен означать target 10 game ticks/sec,
а не fixed one tick every 0.1s плюс overhead.
```

## Этап 4 — tick_many / batch advancement

Добавить:

```python
tick_match_many(match_id, db, max_ticks)
ZoneStalkerRuleSet.tick_many(...)
tick_zone_map_many(...)
```

Batch должен:

```text
load state once
run N world ticks in memory
insert events in batch
save state once
commit once
build one delta
broadcast once/coalesced
```

## Этап 5 — reduce DB overhead

```text
- skip active zone_event query if state.active_events empty;
- avoid duplicate zone_ctx query in ticker;
- skip commit when no DB changes;
- batch event inserts.
```

## Этап 6 — reduce state serialization overhead

```text
- configurable zlib level;
- batch Redis save;
- optional state size metrics;
- ensure no full projection/HTTP refresh is triggered per tick.
```

---

# 17. Pseudo-code: accumulator auto-tick

```python
SPEED_MULTIPLIERS = {
    "realtime": 1,
    "x10": 10,
    "x100": 100,
    "x600": 600,
}

_RUNTIME = {}


def tick_debug_auto_matches_accumulator() -> dict:
    db = SessionLocal()
    try:
        ctx_map = _refresh_debug_context_cache(db)
        now = time.monotonic()
        total_advanced = 0

        for ctx_id, match_id in ctx_map.items():
            auto = read_auto_tick_runtime_key(ctx_id)
            if not auto.enabled:
                reset_runtime_accumulator(ctx_id, now)
                continue

            rt = _RUNTIME.setdefault(ctx_id, {
                "last_ts": now,
                "game_seconds_accum": 0.0,
                "running": False,
            })

            if rt["running"]:
                continue

            elapsed = max(0.0, now - rt["last_ts"])
            rt["last_ts"] = now

            speed = SPEED_MULTIPLIERS.get(auto.speed, 100)
            rt["game_seconds_accum"] += elapsed * speed

            due_ticks = int(rt["game_seconds_accum"] // 60.0)
            if due_ticks <= 0:
                continue

            due_ticks = min(due_ticks, MAX_TICKS_PER_BATCH)
            rt["game_seconds_accum"] -= due_ticks * 60.0

            rt["running"] = True
            try:
                result = tick_match_many(match_id, db, max_ticks=due_ticks)
                total_advanced += result.get("ticks_advanced", 0)
            finally:
                rt["running"] = False

        return {"ticked": total_advanced}
    finally:
        db.close()
```

---

# 18. Pseudo-code: tick_match_many

```python
def tick_match_many(match_id: str, db: Session, max_ticks: int) -> dict:
    match = get_match(...)
    ruleset = get_ruleset(match.game_id)

    started = perf_counter()
    result = ruleset.tick_many(match_id, db, max_ticks=max_ticks)
    total_ms = ...

    if "error" not in result:
        maybe_broadcast_batch_delta(match_id, result)
        record_metrics(...)

    return result
```

---

# 19. Pseudo-code: ZoneStalkerRuleSet.tick_many

```python
def tick_many(self, match_id: str, db: Session, max_ticks: int) -> dict:
    match = query_match_once()
    zone_ctx = query_zone_ctx_once()

    state = load_context_state(zone_ctx.id, zone_ctx)
    old_state = state

    new_state, map_events, ticks_advanced, stop_reason = tick_zone_map_many(
        state,
        max_ticks=max_ticks,
    )

    new_state["state_revision"] = int(state.get("state_revision", 0)) + 1
    new_state["_debug_revision"] = int(state.get("_debug_revision", 0)) + 1

    new_events_for_ws = insert_events_batch(map_events)

    if new_state.get("active_events"):
        process_active_zone_events()

    state_db_written = save_context_state(...)

    if map_events or state_db_written or match_status_changed:
        db.commit()

    zone_delta = build_zone_delta(old_state, new_state, new_events_for_ws)

    return {
        "context_id": str(zone_ctx.id),
        "ticks_advanced": ticks_advanced,
        "stop_reason": stop_reason,
        "world_turn": new_state.get("world_turn"),
        "new_events": new_events_for_ws,
        "zone_delta": zone_delta,
    }
```

---

# 20. Pseudo-code: tick_zone_map_many with one deepcopy

```python
def tick_zone_map_many(state: dict, max_ticks: int):
    state = copy.deepcopy(state)
    all_events = []
    ticks_advanced = 0
    stop_reason = None

    for _ in range(max_ticks):
        state, events = tick_zone_map(state, copy_state=False)
        all_events.extend(events)
        ticks_advanced += 1

        stop_reason = should_stop_batch(state, events)
        if stop_reason:
            break

    return state, all_events, ticks_advanced, stop_reason
```

Modify single tick:

```python
def tick_zone_map(state: dict, *, copy_state: bool = True):
    if copy_state:
        state = copy.deepcopy(state)
    ...
```

---

# 21. Tests

## 21.1. Speed accumulator tests

```text
[ ] x600 for 1.0 real second produces about 10 due ticks.
[ ] x100 for 0.6 real seconds produces about 1 due tick.
[ ] fractional accumulator carries remainder.
[ ] due_ticks is capped by max_ticks_per_batch.
```

## 21.2. Batch tick tests

```text
[ ] tick_many(max_ticks=10) advances world_turn by 10.
[ ] tick_many emits day_changed if day boundary crossed.
[ ] tick_many stops on game_over.
[ ] tick_many stops on emission_started if configured.
[ ] tick_many saves state once.
[ ] tick_many builds one delta.
```

## 21.3. Compatibility tests

```text
[ ] single tick result remains compatible.
[ ] manual /tick still advances by 1.
[ ] debug_advance_turns still works.
[ ] auto_tick_changed still works.
[ ] frontend can apply batch zone_delta.
```

## 21.4. DB/Redis tests

```text
[ ] set_auto_tick writes lightweight runtime key.
[ ] tick_debug_auto_matches reads runtime key without full state flag reads.
[ ] missing runtime key falls back safely.
[ ] active_events empty skips event context query.
```

---

# 22. Acceptance criteria

## Functional

```text
[ ] x10/x100/x600 still mean the same target game speed.
[ ] UI receives correct time updates.
[ ] No concurrent ticks for same context.
[ ] Batch does not skip important stop events.
[ ] Manual tick remains one minute.
[ ] Existing saves remain compatible.
```

## Performance

```text
[ ] Auto-ticker no longer adds fixed 100ms sleep to every game tick.
[ ] x600 can process multiple due ticks per backend update.
[ ] Redis full-state decompression is not used for every auto_tick flag read.
[ ] Batch mode reduces Redis load/save calls by approximately batch size.
[ ] Batch mode reduces DB commits by approximately batch size.
[ ] Batch mode reduces WS broadcasts by approximately batch size or configured cadence.
[ ] Effective speed metric is visible.
```

## Safety

```text
[ ] max_ticks_per_batch prevents runaway catch-up.
[ ] critical events can force immediate broadcast.
[ ] errors in one context do not stop ticker globally.
[ ] context lock prevents parallel mutation.
```

---

# 23. Expected gains

## Without changing Brain v3

Expected improvements come from reducing fixed overhead:

```text
- fewer state load/decompress operations;
- fewer deepcopy operations in batch;
- fewer JSON dump/compress operations;
- fewer Redis writes;
- fewer DB commits;
- fewer WS broadcasts;
- fewer duplicate DB queries;
- no full-state decompress for auto_tick flags.
```

## Rough estimates

For high-speed auto-run:

```text
x100 / x600 with batch size 5–10:
  fixed backend overhead can drop ~5×–10×.
```

If current bottleneck is mostly state serialization/compression/DB/WS, effective speed can improve dramatically without touching NPC Brain.

If current bottleneck is Brain v3, this PR still helps but later CPU PR is needed.

---

# 24. Recommended minimal PR scope

If we want a practical, not-too-large PR, do this:

```text
1. Add speed accumulator.
2. Add lightweight auto_tick Redis key.
3. Add tick_match_many / tick_many / tick_zone_map_many.
4. Add copy_state=False mode to tick_zone_map for batch.
5. Add batch save/commit/ws once per batch.
6. Skip active zone_event query when active_events empty.
7. Add effective_speed metrics.
```

Do **not** include in this PR:

```text
- full dirty-set system;
- full copy-on-write runtime;
- lazy needs;
- event-driven scheduled actions;
- memory indexes;
- pathfinding caches.
```

Those belong to the next heavy CPU optimization PR.

---

# 25. Summary

The main issue with `x600` is not only raw CPU in NPC Brain.

The current process pays full per-minute overhead:

```text
sleep + load + decompress + deepcopy + tick + compress + save + db + delta + ws
```

For `x600`, this happens up to 10 times per second.

The better model is:

```text
accumulate real elapsed time
→ calculate due game minutes
→ process due minutes in batch
→ save/broadcast at controlled cadence
```

This optimizes the process itself and benefits every map state, not only empty maps.
