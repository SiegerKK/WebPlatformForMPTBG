# Вопросы по NPC Brain v3 — Раунд 2

> Контекст: прочитал ответы из `docs/npc_v3_copilot_answer_and_redis.md` и изучил реальный код.
> Все вопросы конкретны — касаются точек стыка между v3 и уже существующими модулями.

---

## 1. Точка входа в `tick_rules.py` для `PlanMonitor`

В `tick_rules.py` есть две ключевые функции:

```text
_process_scheduled_action   — обрабатывает активное длительное действие
_run_bot_decision_v2_inner  — запускает полный decision pipeline (context → needs → intent → plan → executor)
```

Сейчас `_process_scheduled_action` вызывается **вместо** `_run_bot_decision_v2_inner`, если у агента есть `scheduled_action`. Это и есть проблема — НПЦ не переоценивает план.

Вопрос: **куда вставлять `PlanMonitor` в рамках PR 1?**

Вариант A:
```text
В начале _process_scheduled_action:
  plan_monitor.assess(...) → если решение ABORT/PAUSE → очистить scheduled_action → пустить в _run_bot_decision_v2_inner
```

Вариант B:
```text
Перед развилкой "есть scheduled_action?":
  plan_monitor.assess(...) первым → результат определяет, пойти в _process_scheduled_action или в _run_bot_decision_v2_inner
```

Вариант C:
```text
Оставить _process_scheduled_action нетронутым, но добавить PlanMonitor как отдельный pre-step перед всей логикой тика агента
```

Какой вариант правильный?

---

## 2. `active_plan_v3` — хранение в БД

Агент хранится в PostgreSQL как JSONB-блоб. `active_plan_v3` — это новое поле внутри этого блоба.

Вопросы:

- Нужна ли Alembic-миграция для нового поля, или оно просто появляется в JSON и ничего не ломается?
- Нужно ли добавить `active_plan_v3` в Pydantic-схему агента (если она есть), или пока можно писать напрямую в `agent["active_plan_v3"]`?
- Есть ли ограничение по размеру одного агентского JSONB-блоба, которое может стать проблемой при росте `active_plan_v3`?

---

## 3. `brain_trace` — когда писать и где хранить

Ответ сказал, что backend должен отдавать `agent["brain_trace"]`. Но неясно:

- **Каждый тик** `brain_trace` перезаписывается полностью, или только при изменении плана?
- **Персистируется ли** `brain_trace` в PostgreSQL, или вычисляется on-the-fly при запросе и не сохраняется?
- Если персистируется — `brain_trace` может расти. Нужно ли ограничивать его размер?
- **Кто читает `brain_trace`?** Текущий `AgentProfileModal` через обычный GET `/api/games/zone_stalkers/{game_id}/state`, или нужен отдельный endpoint?

---

## 4. `ItemNeed.compatible_item_types` — сериализация

В ответе показана структура:

```python
@dataclass
class ItemNeed:
    compatible_item_types: frozenset[str]
```

Но `frozenset` не сериализуется в JSON. При записи в `agent["item_needs"]` это сломается.

Вопрос: **как хранить `compatible_item_types`?**

- `list[str]` в модели (и забыть про `frozenset`)
- `frozenset` только в runtime Python-объекте, а в JSON пишется как `list`
- Что-то другое?

---

## 5. `PlanMonitor` и текущая interrupt-логика в `intents.py`

В `intents.py` уже есть hardcoded пороги прерывания:

```python
_HARD_INTERRUPT_SURVIVE_NOW = 0.70
_HARD_INTERRUPT_EMISSION    = 0.80
_HARD_INTERRUPT_HEAL        = 0.80
```

Эти пороги используются при принятии решения о смене intent.

`PlanMonitor` тоже должен проверять survival-критерии (hp critical, emission threat и т.д.).

Вопрос: **не будет ли дублирования?**

- Нужно ли перенести эти константы в `plan_monitor.py` и убрать из `intents.py`?
- Или `PlanMonitor` просто вызывает `evaluate_needs(ctx)` + `select_intent(ctx, needs)` и смотрит на изменение intent — и тогда дублирования нет?
- Или для PR 1 дублирование допустимо и будет убрано позже?

---

## 6. `score_factors` — где живут в состоянии агента

Ответ говорит хранить `score_factors` с первого дня. Но в какой ключ агента?

Варианты:

```json
// Вариант A: внутри brain_trace
agent["brain_trace"]["score_factors"] = [...]

// Вариант B: внутри active_plan_v3
agent["active_plan_v3"]["objective_score_factors"] = [...]

// Вариант C: отдельный ключ
agent["objective_score_debug"] = [...]
```

Какой вариант?

---

## 7. Frontend: endpoint и компонент

Ответ говорит добавить `brain_trace` в `AgentProfileModal`.

Вопросы:

- Файл `frontend/src/games/zone_stalkers/ui/AgentProfileModal.tsx` — это правильное место?
- `brain_trace` будет приходить как часть обычного state-ответа (GET `/state`), или нужен отдельный endpoint?
- Если в `/state` — есть ли ограничения на размер ответа, которые могут вызвать проблемы?
- Нужно ли сразу добавить toggle/collapse для `brain_trace` блока, или можно начать с простого `<pre>` для отладки?

---

## 8. `MemoryStore.ingest()` — совместимость с `agent["memory"]`

Ответ сказал: `agent["memory"]` оставить как compatibility layer.

Значит при вызове `MemoryStore.ingest(agent_id, observations)` нужно решить:

- Новая запись пишется **только** в `agent["memory_v3"]`?
- Или она пишется **и в `memory_v3`, и в `memory`** (чтобы старые модули не сломались)?
- Если оба — как не раздуть `memory` до предела `MAX_AGENT_MEMORY = 2000`?
- Если только `memory_v3` — какие конкретно модули сейчас читают из `agent["memory"]` и могут сломаться?

---

## 9. Расположение новых тестов

Существующие тесты:

```text
backend/tests/decision/test_planner.py
backend/tests/decision/test_intents.py
backend/tests/decision/test_needs.py
...
```

Новые тесты по плану:

```text
test_brain_trace.py
test_active_plan.py
test_plan_monitor.py
test_item_needs.py
```

Вопрос: **куда их класть?**

- В тот же `backend/tests/decision/` — плоским списком?
- В новую папку `backend/tests/decision/v3/`?
- В корень `backend/tests/`?

---

## 10. `explain_intent.py` и новый `brain_trace.py`

Уже существует `backend/app/games/zone_stalkers/decision/debug/explain_intent.py`.

Этот модуль явно делает что-то похожее на `brain_trace` — объясняет принятое решение.

Вопросы:

- `brain_trace.py` **заменяет** `explain_intent.py`, **расширяет** его или живёт рядом?
- Если рядом — не будет ли дублирования ответственности?
- Нужно ли перед PR 1 прочитать `explain_intent.py` и понять, что из него можно переиспользовать?
