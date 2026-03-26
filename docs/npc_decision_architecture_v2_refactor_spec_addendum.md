# Zone Stalkers — NPC Decision Architecture v2
## Addendum / Supplement for Refactoring Spec

## 1. Что я думаю об анализе Copilot

Анализ полезный и в целом точный.

Он хорошо усиливает исходный refactor spec в четырёх местах:

1. **Добавляет инженерную конкретику**, которой не хватало:
   - формулы `NeedScores`,
   - правила тайминга диалогов,
   - стратегия хранения `RelationState`,
   - миграционная тактика для `scheduled_action`.

2. **Правильно выделяет риски реализации**, а не только архитектурные идеи:
   - O(N²) для отношений,
   - конфликт старых runtime-структур и новых plan-структур,
   - сложность групп как первой truly coordinated mechanic.

3. **Правильно рекомендует staged migration**, а не big bang rewrite.

4. **Правильно указывает**, что текущая спецификация описывает в основном “что строить”, но ещё не до конца описывает “как именно”.

То есть я бы не спорил с направлением анализа.  
Я бы сделал следующий шаг: не переписывать базовый документ, а **расширить его инженерным приложением** — тем, что именно нужно для реального старта рефакторинга.

Это приложение и зафиксировано ниже.

---

# 2. Что нужно добавить к базовому refactor spec

Ниже — конкретные дополнения, которых не хватало в исходной версии документа.

---

# 3. Конкретные Python-модели

Базовая архитектура должна не только называться, но и иметь первичные типы данных.

## 3.1. AgentContext

```python
from dataclasses import dataclass, field
from typing import Any, Optional

@dataclass
class AgentContext:
    agent_id: str
    self_state: dict[str, Any]
    location_state: dict[str, Any]
    world_context: dict[str, Any]
    visible_entities: list[dict[str, Any]] = field(default_factory=list)
    known_entities: list[dict[str, Any]] = field(default_factory=list)
    known_locations: list[dict[str, Any]] = field(default_factory=list)
    known_hazards: list[dict[str, Any]] = field(default_factory=list)
    known_traders: list[dict[str, Any]] = field(default_factory=list)
    known_targets: list[dict[str, Any]] = field(default_factory=list)
    current_commitment: Optional[dict[str, Any]] = None
    combat_context: Optional[dict[str, Any]] = None
    social_context: Optional[dict[str, Any]] = None
    group_context: Optional[dict[str, Any]] = None
```

## 3.2. NeedScores

```python
from dataclasses import dataclass

@dataclass
class NeedScores:
    survive_now: float = 0.0
    heal_self: float = 0.0
    eat: float = 0.0
    drink: float = 0.0
    sleep: float = 0.0
    reload_or_rearm: float = 0.0
    get_rich: float = 0.0
    hunt_target: float = 0.0
    unravel_zone_mystery: float = 0.0
    avoid_emission: float = 0.0
    trade: float = 0.0
    negotiate: float = 0.0
    maintain_group: float = 0.0
    help_ally: float = 0.0
    join_group: float = 0.0
    leave_zone: float = 0.0
```

## 3.3. Intent

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class Intent:
    kind: str
    score: float
    source_goal: Optional[str] = None
    target_id: Optional[str] = None
    target_location_id: Optional[str] = None
    reason: Optional[str] = None
    created_turn: Optional[int] = None
    expires_turn: Optional[int] = None
```

## 3.4. PlanStep и Plan

```python
from dataclasses import dataclass, field
from typing import Any, Optional

@dataclass
class PlanStep:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    interruptible: bool = True
    expected_duration_ticks: int = 1

@dataclass
class Plan:
    intent_kind: str
    steps: list[PlanStep] = field(default_factory=list)
    current_step_index: int = 0
    interruptible: bool = True
    confidence: float = 0.5
    created_turn: Optional[int] = None
    expires_turn: Optional[int] = None
```

## 3.5. RelationState

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class RelationState:
    attitude: str = "neutral"
    trust: float = 0.0
    fear: float = 0.0
    respect: float = 0.0
    hostility: float = 0.0
    debt: float = 0.0
    faction_bias: float = 0.0
    shared_history_score: float = 0.0
    known_reliability: float = 0.0
    last_interaction_type: Optional[str] = None
    last_interaction_turn: Optional[int] = None
```

## 3.6. GroupState

```python
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class GroupState:
    group_id: str
    leader_id: str
    members: list[str] = field(default_factory=list)
    shared_goal: Optional[str] = None
    shared_plan: Optional[dict] = None
    hierarchy: dict[str, str] = field(default_factory=dict)
    status: str = "active"
    formation_turn: Optional[int] = None
```

---

# 4. Формулы расчёта NeedScores

Их действительно нужно зафиксировать до начала активной реализации.

## 4.1. Базовый принцип
Каждая потребность возвращает значение в диапазоне `0.0 .. 1.0`.

## 4.2. Базовые формулы

```python
survive_now = 1.0 if hp <= 10 else max(0.0, (30 - hp) / 30)
heal_self   = 1.0 if hp <= 20 else max(0.0, (50 - hp) / 50)

eat   = hunger / 100.0
drink = thirst / 100.0
sleep = sleepiness / 100.0
```

## 4.3. Боеспособность
```python
has_weapon_score = 0.0 if not has_weapon else 1.0
has_ammo_score   = min(1.0, ammo_count / desired_ammo_reserve)
reload_or_rearm  = 1.0 - min(has_weapon_score, has_ammo_score)
```

## 4.4. Wealth gate
Wealth gate оставляем, но описываем как функцию.

```python
wealth_ratio = min(1.0, wealth / max(1, material_threshold))
get_rich = (1.0 - wealth_ratio) * 0.7
```

## 4.5. Глобальные цели с учётом wealth gate

### Для `get_rich`
```python
goal_pressure = max(0.3, 1.0 - wealth_ratio)
```

### Для `kill_stalker`
```python
goal_pressure = base_goal_urgency * max(0.25, wealth_ratio)
```

### Для `unravel_zone_mystery`
```python
goal_pressure = base_goal_urgency * max(0.4, wealth_ratio)
```

### Важный компромисс
Мы оставляем wealth gate, но не делаем его абсолютным стопором.  
То есть даже бедный охотник может охотиться, просто не так агрессивно.

---

# 5. Правила tie-break и нормализации

Если два NeedScore близки, система должна разруливать это детерминированно.

## 5.1. Рекомендуемый порядок tie-break
1. `survive_now`
2. `heal_self`
3. `avoid_emission`
4. `drink`
5. `eat`
6. `sleep`
7. `reload_or_rearm`
8. `maintain_group`
9. `help_ally`
10. `trade`
11. `get_rich`
12. `hunt_target`
13. `unravel_zone_mystery`
14. `negotiate`
15. `join_group`

## 5.2. Зачем это нужно
Чтобы NPC в пограничных состояниях не дёргался между двумя одинаково важными решениями.

---

# 6. Политика прерывания Intent и Plan

Copilot правильно заметил, что без этого intent-layer будет красивым только на бумаге.

## 6.1. Категории прерывания

### Hard interrupt
Прерывает всё:
- выброс;
- вход в боевое взаимодействие;
- критическое ранение;
- смерть лидера группы;
- потеря текущей локации как безопасной.

### Soft interrupt
Может перепланировать, но не обязана:
- появление торговца;
- новое воспоминание;
- замеченный союзник;
- свежая разведданная о цели;
- предложение вступить в группу.

### No interrupt
Не должно ломать:
- косметические наблюдения;
- слабые сигналы;
- незначительные изменения hunger/thirst.

## 6.2. Правило
Каждый `PlanStep` обязан иметь `interruptible`.

---

# 7. Совместимость с текущим scheduled_action

Это критический мост между старым кодом и новой архитектурой.

## 7.1. Пока migration не завершён

`scheduled_action` считается:
> сериализованным текущим шагом плана.

## 7.2. Mapping

### Старое
```python
agent["scheduled_action"] = {
  "type": "travel",
  ...
}
```

### Новое
```python
PlanStep(kind="travel_to_location", payload={...})
```

## 7.3. Временное правило
Пока migration не закончена:
- если у агента есть `scheduled_action`, но нет `plan`,
- нужно уметь реконструировать `Plan` из `scheduled_action`.

### Helper
Нужен helper:
- `plan_from_scheduled_action(agent) -> Plan`

---

# 8. Visibility model

Это место в анализе Copilot правильно отмечено как недоописанное.

## 8.1. MVP visibility
Пока достаточно простой модели:

NPC “видит”:
1. всех агентов на той же локации;
2. все объекты на той же локации;
3. недавнюю память о соседних локациях;
4. переданные в диалоге воспоминания;
5. intel от торговцев;
6. сигналы группы.

## 8.2. Значение
Это становится базой для:
- `visible_entities`
- `known_entities`
- `known_targets`

---

# 9. Хранение социальных отношений

Copilot правильно указал на риск O(N²).

## 9.1. Решение
Использовать **ленивую инициализацию**.

### Структура:
```python
state["relations"][agent_id][other_agent_id] = RelationState(...)
```

## 9.2. Правило
Если пары нет в `state["relations"]`, отношение считается:
- `attitude = "neutral"`
- остальные значения = `0.0`

## 9.3. Почему это лучше
- не нужно заранее создавать отношения между всеми;
- память и социальная сеть растут только по факту взаимодействий.

---

# 10. Тайминг диалогов

Этого действительно не хватало.

## 10.1. MVP-тайминг
Простой диалог занимает:
- `1 тик` — короткий обмен (“видел цель?”, “торгуешь?”)
- `2 тика` — обмен воспоминаниями
- `3 тика` — предложение группы / сложная сделка

## 10.2. Прерываемость
Диалог:
- прерывается hard interrupt-ами;
- может быть отменён смертью или уходом одного участника;
- должен записывать неполный результат, если оборвался.

## 10.3. DialogueSession lifecycle
- `created`
- `active`
- `resolved`
- `interrupted`
- `aborted`

---

# 11. Группы: условия роспуска и смена лидера

Это действительно важный пробел.

## 11.1. Группа распускается, если:
- остался 1 участник;
- у участников появился непримиримый конфликт;
- общая цель больше не совпадает;
- лидер исчез и новый лидер не может быть назначен;
- участники физически разошлись и cohesion упал ниже порога.

## 11.2. Смена лидера
Если лидер:
- погиб,
- ушёл,
- покинул группу,
- потерял доверие,

то новый лидер выбирается по score:

```text
leader_score =
  respect * 0.30 +
  trust * 0.15 +
  competence * 0.25 +
  health_ratio * 0.10 +
  commitment_to_goal * 0.20
```

## 11.3. Что делать с планом группы
При смене лидера:
- `shared_plan` не уничтожается сразу;
- группа получает `reassess_group_plan` intent;
- новый лидер может:
  - принять старый план,
  - изменить,
  - распустить группу.

---

# 12. Совмещение group plan и emergency needs

Это нужно зафиксировать явно.

## 12.1. Правило
Личный emergency drive имеет право поднять group need.

### Пример:
Если у одного члена:
- `survive_now >= 0.9`
или
- `heal_self >= 0.85`

то это поднимает групповой drive:
- `protect_weak_member`
- `heal_member`
- `rest_group`
в зависимости от ситуации.

## 12.2. Следствие
`follow_group_plan` никогда не должен быть абсолютным приказом.

---

# 13. Минимальный MVP групп

Чтобы не перегрузить систему, группы нужно вводить поэтапно.

## 13.1. Первая версия групп
Только:
- группа из 2 NPC;
- роли `leader/member`;
- shared goal;
- follow_leader_travel;
- regroup;
- share_intel;
- simple mutual support.

## 13.2. Чего пока не делать
- сложные боевые построения;
- split/merge squads;
- голосование;
- многоступенчатую иерархию;
- распределение ролей в реальном времени.

---

# 14. Стратегия тестирования

Этого тоже не хватало в исходном документе.

## 14.1. Golden tests
Существующие тесты world-tick поведения должны стать golden tests:
- одинаковый seed
- одинаковый NPC state
- одинаковый expected decision path

## 14.2. Новые тестовые наборы

### Для AgentContext
- co-located visibility
- memory-based knowledge
- trader intel inclusion

### Для NeedScores
- hp thresholds
- hunger/thirst/sleepiness gradients
- wealth gate scaling

### Для Intent
- deterministic tie-break
- interrupt handling
- goal transition

### Для Dialogue
- memory exchange
- interrupted dialogue
- proposal to form group

### Для Groups
- group formation
- leader death
- regroup due to weak member
- group dissolution

---

# 15. Performance guardrails

Чтобы архитектура не убила tick loop.

## 15.1. AgentContext
Должен строиться один раз на тик на агента.

## 15.2. Relations
Ленивая инициализация.

## 15.3. Group updates
Пересчитывать только если:
- кто-то присоединился,
- кто-то умер,
- кто-то ушёл,
- или changed shared_goal.

## 15.4. Dialogue sessions
Обрабатывать отдельным менеджером, как сейчас обрабатываются combat interactions.

---

# 16. Debug / Explainability

Это критично для новой архитектуры.

## 16.1. Каждый тик для NPC должен быть доступен explain output:
- top NeedScores
- selected Intent
- active Plan
- current PlanStep
- active interrupts
- current group state
- current relation summary

## 16.2. Для Copilot и команды
Нужен helper:
- `describe_agent_decision(agent_id, state) -> dict|str`

---

# 17. Конкретные новые документы

После этого дополнения уже прямо нужны такие файлы:

1. `npc_decision_entities_v2.md`
2. `need_scores_formulas_v1.md`
3. `social_model_v1.md`
4. `dialogue_sessions_v1.md`
5. `group_ai_v1.md`
6. `migration_plan_from_tick_rules.md`
7. `decision_debug_contract.md`

---

# 18. Итог

Базовый refactor spec остаётся правильным по архитектуре.  
Анализ Copilot справедливо показал, что для реальной реализации не хватало:

- Python-моделей,
- формул,
- таймингов,
- interrupt policy,
- storage policy,
- migration bridge,
- правил для групп,
- тестовой стратегии.

Это дополнение закрывает именно эти пробелы.

Если кратко:

> Исходный документ задаёт правильную архитектуру.  
> Это дополнение превращает её в инженерно исполнимую спецификацию.
