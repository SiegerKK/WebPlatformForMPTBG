# Вопросы по NPC Brain v3 — Раунд 4

> Контекст: прочитал `docs/npc_v3_questions_round3_answer.md` и сверил предложения с текущим кодом в `tick_rules.py`.
> Ниже — только новые вопросы, которые остались не до конца определены перед PR 1.

---

## 1. `project_agent_needs_after_tick`: где source of truth для коэффициентов?

В ответе раунда 3 предлагается `project_agent_needs_after_tick()` с теми же коэффициентами, что и в шаге деградации:

- hunger `+3`
- thirst `+5`
- sleepiness `+4`
- hp `-2/-1` при thirst/hunger `>=80`

Сейчас эти значения захардкожены в `tick_zone_map()` (блок 2).  
Вопрос: как избежать рассинхронизации между реальной деградацией и projected helper?

Варианты:
- A) вынести коэффициенты в общий модуль/константы и использовать в обоих местах;
- B) оставить дублирование в PR 1 и закрыть отдельным refactor PR;
- C) вообще не проектировать деградацию, а принять текущий порядок тика как есть.

---

## 2. Двойной запуск pipeline в одном тике: ограничиваем ли?

Сценарий:
1. `PlanMonitor` делает `abort`.
2. В том же тике бот идёт в `_run_bot_decision_v2_inner`.

Это правильно по логике, но вычислительно тяжелее (ещё один `build_context → evaluate_needs → select_intent → build_plan`).

Вопрос: нужен ли guard против повторных heavy-решений в одном тике (например, максимум 1 replan after abort), или для PR 1 оставляем как есть?

---

## 3. `brain_trace.events`: финальный контракт для фронта

В раунде 3 предлагается хранить `brain_trace.events` (до 5 событий), но в текущем фронте типы под это ещё не зафиксированы.

Вопрос: какой минимальный стабильный контракт закрепляем уже в PR 1?

Например:
- обязательные поля event: `mode`, `decision`, `reason?`, `timestamp/turn?`;
- допустимые `mode`: `plan_monitor | decision`;
- допустимые `decision`: `continue | abort | new_intent | ...`;
- ограничение `events <= 5`.

Нужно ли этот контракт сразу описать в TS-типе `AgentForProfile`, чтобы не плодить `unknown`?

---

## 4. `active_plan_v3` lifecycle при нормальном завершении action

Если travel/explore/sleep завершается без abort:
- `_process_scheduled_action()` снимает `scheduled_action`;
- может стартовать следующий action из `action_queue`;
- либо action полностью завершён.

Вопрос: что в этот момент делать с `active_plan_v3`?

Варианты:
- A) помечать `status="completed"` и очищать объект;
- B) оставлять последний snapshot как debug (`status="completed"`, не удалять);
- C) не трогать в PR 1, пока `active_plan_v3` остаётся только retrofit metadata.

---

## 5. `action_queue` при `pause/adapt` (future-safe API уже сейчас?)

В PR 1 реально используем `continue/abort`, но `PlanMonitorResult` уже обсуждается с `pause/adapt`.

Вопрос: стоит ли в PR 1 сразу заложить чёткий контракт:
- `abort` → clear queue;
- `pause` → сохранить queue;
- `adapt` → заменить head queue + сохранить tail;

или не фиксировать это заранее, чтобы не обещать поведение, которого пока нет?

---

## 6. Память при `abort`: как дедуплицировать повторяющиеся прерывания?

Если агент 3–4 тика подряд получает `abort` по одной причине (например, критическая жажда), можно засорить `agent["memory"]` decision-записями.

Вопрос: нужен ли dedup/throttle уже в PR 1?

Например:
- не писать новый `plan_monitor_travel_aborted`, если предыдущий такой же (`reason + cancelled_final_target`) был < N тиков назад;
- либо писать всегда (проще), а чистку оставить на memory_v3.

---

## 7. Совместимость с human-агентом, у которого есть `scheduled_action`

По решению раунда 3 `PlanMonitor` не трогает humans, но `_process_scheduled_action()` остаётся общим.

Вопрос: нужно ли отдельным тестом зафиксировать инвариант:

```text
human agent + scheduled_action
→ PlanMonitor НЕ вызывается
→ behavior полностью legacy
```

Чтобы исключить регресс при будущих рефакторах фильтра?

---

## 8. Какие event'ы наружу при abort обязаны быть стабильными?

В раунде 3 предложен новый event:

```json
{"event_type":"plan_monitor_aborted_action", ...}
```

Вопрос: нужен ли этот event в публичном контракте `/state` уже в PR 1, или достаточно memory + brain_trace без нового `event_type`?

Если нужен, зафиксировать ли минимальный payload:
- `agent_id`
- `scheduled_action_type`
- `reason`
- `dominant_pressure`

---

## 9. Где физически размещать helper `_is_v3_monitored_bot`

Это маленькая, но критичная функция фильтрации.

Вопрос: куда её лучше положить для поддержки тестами:
- A) рядом в `tick_rules.py` как private helper;
- B) в `decision/plan_monitor.py` и импортировать в `tick_rules.py`;
- C) в отдельный `decision/agent_filters.py`.

Что предпочтительно для PR 1 (минимум изменений + удобство unit tests)?

---

## 10. Минимальный acceptance-критерий для "есть brain_trace у всех bot stalkers"

Раунд 3 зафиксировал: `brain_trace` должен писаться и из PlanMonitor, и из `_run_bot_decision_v2_inner`.

Вопрос: какой testable критерий принимаем:

- A) после `tick_zone_map` у каждого живого bot stalker всегда есть `brain_trace.turn == world_turn_before_increment`;
- B) только у тех, кто реально участвовал в decision/monitor ветке;
- C) допускаем пропуски при ранних return ветках (`_bot_pickup_on_arrival`, `_bot_sell_on_arrival`, `_pre_decision_equipment_maintenance`)?

Нужна чёткая формулировка, чтобы тесты не спорили с intended behavior.
