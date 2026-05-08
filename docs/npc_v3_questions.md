# Уточняющие вопросы по архитектуре NPC Brain v3

Перед началом реализации нужны ответы на следующие вопросы.

---

## 1. Отправная точка и скоуп

Документ описывает 6 этапов миграции (§12). **С какого начинаем?**

- Этап 1 — добавить `brain_trace`, `active_plan_v3`, не трогая существующую механику
- Этап 3 — разбить `reload_or_rearm` на отдельные `ItemNeed`
- Полный MVP — `NPCBrain`, `BeliefState`, `MemoryStore`, `DriveEvaluator`, `ObjectiveGenerator`, `PlanMonitor` за один заход

Или сначала хочешь полный план-список изменений и файлов, а потом подтвердить?

---

## 2. Память: хранение

Сейчас память живёт в `agent["memory"] = []` внутри JSON-поля в PostgreSQL.

- Новая многослойная память (Working / Episodic / Semantic / Spatial / Social / Threat / Goal) остаётся в JSON-поле агента или переезжает в отдельную таблицу с индексами?
- `agent["memory"]` нужно поддерживать как совместимый слой или полностью заменить?
- Decay/consolidation — часть обычного тика или отдельная фоновая задача?

---

## 3. `BeliefState` vs `AgentContext`

Сейчас есть `AgentContext` (строится в `context_builder.py`).

- `BeliefState` полностью заменяет `AgentContext` или является его надстройкой?
- Ограничение «НПЦ видит только то, что знает, а не весь state» — архитектурное ограничение сразу или только в будущем?

---

## 4. `Drive` vs `NeedScores`

- Нужно сразу заменить `NeedScores` на `Drive` с полями `urgency / importance / confidence / source_factors`?
- Или пока только разбить `reload_or_rearm` на отдельные item needs, а остальное потом?

---

## 5. Objective scoring (§6.6)

Формула включает `expected_value`, `success_probability`, `memory_confidence`, `goal_alignment`, `plan_switch_cost` — большинство требуют новой инфраструктуры.

- Что входит в MVP? Можно пока `urgency * weight - risk * risk_sensitivity`, или нужна полная формула?

---

## 6. `PlanMonitor` и инерция плана

- `switch_cost` — фиксированное значение (например `0.10`) или динамическое (зависит от оставшихся шагов/времени)?
- `PlanMonitor` — новый модуль или часть `intents.py` / `planner.py`?
- Пока `scheduled_action` не заменён на `ActivePlan`, что является источником `remaining_ticks`?

---

## 7. `scheduled_action` → `ActivePlan`

- Как они соотносятся в переходный период? `ActivePlan` генерирует `scheduled_action` через `bridges.py`, или `scheduled_action` убирается полностью?

---

## 8. Personality

В §15 есть `agent.get("personality", {})`, передаётся в `choose_decision`. Сейчас только `risk_tolerance`.

- Какие поля нужны для MVP: только `risk_tolerance`, или уже добавить `greed`, `loyalty`, `caution` и т.д.?

---

## 9. Frontend

§10 описывает `NPC Thought Panel` (drives, alternatives, memory_used, interrupt_watchlist).

- Frontend входит в scope v3, или сначала только backend?
- Если да — есть готовый React-компонент для debug-панели или делаем новый?

---

## 10. Тесты

- Старые тесты (`test_needs`, `test_planner` и др.) нужно переписать вместе с v3 или поддерживать параллельно до полной миграции?
