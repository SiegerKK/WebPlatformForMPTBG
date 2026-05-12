# Zone Stalkers — PR plan: spawn-stalker crash fix and multi-image location slots

## Цель PR

В этом PR нужно закрыть две связанные задачи debug-карты `Zone Stalkers`:

1. Исправить баг в `main`, при котором после спавна сталкера на локации падает фронт.
2. Расширить модель изображений локации: вместо одного `image_url` добавить 5 смысловых слотов:
   - `clear` — ясно;
   - `fog` — туман;
   - `rain` — дождь;
   - `night_clear` — ночь ясно;
   - `night_rain` — ночь дождь.

Дополнительно нужно переработать debug UI карты:

- карта должна стать немного уже;
- блок деталей локации должен стать шире;
- внутренние секции detail panel должны быть адаптированы, чтобы текст/кнопки/таблицы/картинки не вылезали;
- картинка локации в detail panel должна отображаться крупнее;
- у каждой локации должна быть приоритетная картинка, которая отображается в обычном интерфейсе и debug UI;
- в debug UI нужно уметь переключать приоритетный image slot кнопками.

---

# 1. Текущий контекст в main

## 1.1. Backend spawn-stalker flow

В `backend/app/games/zone_stalkers/rules/world_rules.py` команда `debug_spawn_stalker`:

- валидируется как debug meta-command;
- создаёт нового stalker через `_make_stalker_agent`;
- добавляет его в `state["agents"]`;
- добавляет id нового агента в `state["locations"][loc_id]["agents"]`;
- возвращает event `debug_stalker_spawned`.

Сейчас логика выглядит концептуально так:

```python
if command_type == "debug_spawn_stalker":
    loc_id = payload["loc_id"]
    existing_agents = state.get("agents", {})
    n = len(existing_agents)
    new_agent_id = f"agent_debug_{n}"

    agent = _make_stalker_agent(...)
    state.setdefault("agents", {})[new_agent_id] = agent
    state["locations"][loc_id]["agents"].append(new_agent_id)

    events.append({
        "event_type": "debug_stalker_spawned",
        "payload": {
            "agent_id": new_agent_id,
            "loc_id": loc_id,
        },
    })
    return state, events
```

## 1.2. Frontend detail panel flow

В `frontend/src/games/zone_stalkers/ui/debugMap/DetailPanels.tsx` detail panel строит список персонажей из `loc.agents`:

```ts
const allPersons: PersonEntry[] = loc.agents.flatMap((id): PersonEntry[] => {
  const stalker = zoneState.agents[id];
  if (stalker) return [{ ... }];
  const trader = zoneState.traders[id];
  if (trader) return [{ ... }];
  return [];
});
```

Мутанты строятся отдельно:

```ts
const mutants = loc.agents.map((id) => zoneState.mutants[id]).filter(Boolean);
```

То есть `loc.agents` является общим списком occupants id, где могут быть:

```text
agents
traders
mutants
```

Падение фронта после спавна сталкера почти наверняка связано с нарушением одной из этих инвариант:

```text
- loc.agents не массив;
- zoneState.agents отсутствует или undefined;
- zoneState.traders отсутствует или undefined;
- zoneState.mutants отсутствует или undefined;
- новый agent имеет не все поля, которые ожидает UI;
- после команды пришёл partial/delta state, где loc.agents уже содержит id, но zoneState.agents ещё не содержит объект;
- selected loc в локальном UI устарел относительно zoneState;
- detail panel пытается рендерить undefined-поля без fallback.
```

---

# 2. Исправление падения фронта после спавна сталкера

## 2.1. Главная гипотеза

С высокой вероятностью падает не сам backend spawn, а frontend render после state update.

Типичный сценарий:

```text
1. Пользователь нажимает Spawn Stalker.
2. Backend добавляет id в location.agents и объект в state.agents.
3. Frontend получает обновление.
4. Detail panel рендерит loc.agents.
5. Один из id временно не резолвится в agents/traders/mutants или agent object не содержит ожидаемого поля.
6. UI падает.
```

Даже если backend возвращает полный корректный state, frontend должен быть устойчивым к промежуточным и legacy-состояниям.

## 2.2. Required frontend hardening

В `DetailPanels.tsx` нельзя напрямую полагаться, что все коллекции существуют.

Сделать безопасные значения:

```ts
const agentsById = zoneState.agents ?? {};
const tradersById = zoneState.traders ?? {};
const mutantsById = zoneState.mutants ?? {};
const occupantIds = Array.isArray(loc.agents) ? loc.agents : [];
```

Заменить:

```ts
const allPersons: PersonEntry[] = loc.agents.flatMap(...)
const mutants = loc.agents.map(...)
```

на:

```ts
const occupantIds = Array.isArray(loc.agents) ? loc.agents : [];

const allPersons: PersonEntry[] = occupantIds.flatMap((id): PersonEntry[] => {
  const stalker = agentsById[id];
  if (stalker) {
    return [{
      id: stalker.id ?? id,
      name: stalker.name ?? id,
      isTrader: false,
      hp: Number.isFinite(stalker.hp) ? stalker.hp : 0,
      max_hp: Number.isFinite(stalker.max_hp) ? stalker.max_hp : 100,
      is_alive: stalker.is_alive ?? true,
      controller: stalker.controller ?? { kind: "bot" },
    }];
  }

  const trader = tradersById[id];
  if (trader) {
    return [{
      id: trader.id ?? id,
      name: trader.name ?? id,
      isTrader: true,
      hp: 100,
      max_hp: 100,
      is_alive: true,
      controller: { kind: "npc" },
    }];
  }

  return [];
});

const mutants = occupantIds
  .map((id) => mutantsById[id])
  .filter((m): m is NonNullable<typeof m> => Boolean(m));
```

## 2.3. Add unknown occupants debug row

Если `loc.agents` содержит id, которого нет ни в одной collection, UI не должен падать и не должен молча терять данные.

Добавить:

```ts
const unknownOccupantIds = occupantIds.filter((id) => (
  !agentsById[id] && !tradersById[id] && !mutantsById[id]
));
```

И в detail panel показать debug-секцию:

```tsx
{unknownOccupantIds.length > 0 && (
  <Section label={`⚠️ Unknown occupants (${unknownOccupantIds.length})`}>
    {unknownOccupantIds.map((id) => (
      <DetailRow key={id}>
        <span style={{ color: '#fca5a5', fontSize: '0.75rem', wordBreak: 'break-all' }}>
          {id}
        </span>
      </DetailRow>
    ))}
  </Section>
)}
```

Это поможет быстро поймать backend/delta рассинхрон.

## 2.4. Backend consistency hardening

В `debug_spawn_stalker` backend должен гарантировать:

```python
state.setdefault("agents", {})
state.setdefault("locations", {})
state["locations"][loc_id].setdefault("agents", [])
```

И не добавлять id повторно:

```python
loc_agents = state["locations"][loc_id].setdefault("agents", [])
if new_agent_id not in loc_agents:
    loc_agents.append(new_agent_id)
```

Также убедиться, что `_make_stalker_agent` возвращает обязательные поля для UI:

```text
id
name
location_id
hp
max_hp
faction
is_alive
controller
inventory
equipment
scheduled_action
```

Если не гарантирует, дополнять в `debug_spawn_stalker`:

```python
agent.setdefault("id", new_agent_id)
agent.setdefault("name", name)
agent.setdefault("location_id", loc_id)
agent.setdefault("hp", 100)
agent.setdefault("max_hp", 100)
agent.setdefault("is_alive", True)
agent.setdefault("controller", {"kind": "bot", "participant_id": None})
agent.setdefault("inventory", [])
agent.setdefault("equipment", {"weapon": None, "armor": None, "detector": None})
agent.setdefault("scheduled_action", None)
agent.setdefault("action_queue", [])
```

## 2.5. Regression test for spawn

Backend test:

```python
def test_debug_spawn_stalker_produces_ui_safe_agent_state():
    state, events = resolve_world_command(
        "debug_spawn_stalker",
        {"loc_id": "loc_A", "name": "Test Stalker"},
        state,
        player_id="debug",
    )

    loc = state["locations"]["loc_A"]
    spawned_id = events[0]["payload"]["agent_id"]

    assert spawned_id in state["agents"]
    assert spawned_id in loc["agents"]

    agent = state["agents"][spawned_id]
    assert agent["id"] == spawned_id
    assert agent["name"]
    assert agent["location_id"] == "loc_A"
    assert "hp" in agent
    assert "max_hp" in agent
    assert "controller" in agent
    assert "inventory" in agent
    assert "equipment" in agent
```

Frontend/manual:

```text
[ ] Открыть debug map.
[ ] Выбрать локацию.
[ ] Нажать Spawn → Сталкер.
[ ] Создать сталкера без имени.
[ ] Создать сталкера с именем.
[ ] Создать несколько сталкеров подряд.
[ ] UI не падает.
[ ] Новый сталкер появляется в списке персонажей.
[ ] Клик по нему открывает профиль.
```

---

# 3. Multi-image slots for locations

## 3.1. New canonical data model

Добавить в location object новое поле:

```ts
type LocationImageSlot =
  | "clear"
  | "fog"
  | "rain"
  | "night_clear"
  | "night_rain";
```

В state:

```json
{
  "image_slots": {
    "clear": "/media/locations/<ctx>/<loc>/clear/<uuid>.jpg",
    "fog": "/media/locations/<ctx>/<loc>/fog/<uuid>.jpg",
    "rain": "/media/locations/<ctx>/<loc>/rain/<uuid>.jpg",
    "night_clear": "/media/locations/<ctx>/<loc>/night_clear/<uuid>.jpg",
    "night_rain": "/media/locations/<ctx>/<loc>/night_rain/<uuid>.jpg"
  },
  "primary_image_slot": "clear",
  "image_url": "/media/locations/<ctx>/<loc>/clear/<uuid>.jpg"
}
```

`image_url` пока оставить как backward-compatible derived/current primary URL.

Canonical source of truth:

```text
location.image_slots
location.primary_image_slot
```

Legacy compatibility:

```text
location.image_url = image_slots[primary_image_slot] ?? first available image slot ?? null
```

## 3.2. Slot labels

Frontend labels:

```ts
export const LOCATION_IMAGE_SLOT_LABELS: Record<LocationImageSlot, string> = {
  clear: "Ясно",
  fog: "Туман",
  rain: "Дождь",
  night_clear: "Ночь ясно",
  night_rain: "Ночь дождь",
};
```

Optional icons:

```ts
clear: "☀️"
fog: "🌫️"
rain: "🌧️"
night_clear: "🌙"
night_rain: "🌧️🌙"
```

## 3.3. TypeScript changes

In `frontend/src/games/zone_stalkers/ui/debugMap/types.ts`:

```ts
export type LocationImageSlot =
  | "clear"
  | "fog"
  | "rain"
  | "night_clear"
  | "night_rain";

export type LocationImageSlots = Partial<Record<LocationImageSlot, string | null>>;

export interface ZoneLocation {
  ...
  image_url?: string | null;
  image_slots?: LocationImageSlots;
  primary_image_slot?: LocationImageSlot | null;
}
```

Add helper:

```ts
export const LOCATION_IMAGE_SLOTS: LocationImageSlot[] = [
  "clear",
  "fog",
  "rain",
  "night_clear",
  "night_rain",
];

export function getPrimaryLocationImageUrl(loc: ZoneLocation): string | null {
  const slot = loc.primary_image_slot;
  if (slot && loc.image_slots?.[slot]) return loc.image_slots[slot] ?? null;
  if (loc.image_url) return loc.image_url;
  for (const key of LOCATION_IMAGE_SLOTS) {
    const url = loc.image_slots?.[key];
    if (url) return url;
  }
  return null;
}
```

Use this helper everywhere instead of direct `loc.image_url`.

---

# 4. Backend command/API changes for image slots

## 4.1. Extend debug_update_location

Currently `debug_update_location` supports:

```text
image_url
```

Extend with:

```text
image_slots
primary_image_slot
```

Validation:

```python
_VALID_LOCATION_IMAGE_SLOTS = frozenset({
    "clear",
    "fog",
    "rain",
    "night_clear",
    "night_rain",
})
```

In resolve:

```python
if "image_slots" in payload:
    incoming = payload.get("image_slots") or {}
    current = loc.setdefault("image_slots", {})
    for slot, url in incoming.items():
        if slot not in _VALID_LOCATION_IMAGE_SLOTS:
            continue
        current[slot] = url or None

if "primary_image_slot" in payload:
    slot = payload.get("primary_image_slot") or None
    loc["primary_image_slot"] = slot

_sync_location_primary_image_url(loc)
```

Helper:

```python
def _sync_location_primary_image_url(loc: dict) -> None:
    slots = loc.get("image_slots") or {}
    primary = loc.get("primary_image_slot")

    if primary and slots.get(primary):
        loc["image_url"] = slots[primary]
        return

    if loc.get("image_url") and not slots:
        return

    for key in ("clear", "fog", "rain", "night_clear", "night_rain"):
        if slots.get(key):
            loc["primary_image_slot"] = key
            loc["image_url"] = slots[key]
            return

    loc["image_url"] = None
```

## 4.2. Upload image endpoint should accept slot

Existing location image upload endpoint should become slot-aware.

Recommended route remains:

```text
POST /zone-stalkers/locations/{context_id}/{location_id}/image
```

Add form field/query param:

```text
slot=clear|fog|rain|night_clear|night_rain
```

Default:

```text
slot=clear
```

If keeping old endpoint compatibility, no-slot upload maps to `clear`.

Response:

```json
{
  "url": "/media/locations/<context>/<loc>/<slot>/<uuid>.jpg",
  "image_url": "/media/locations/<context>/<loc>/<slot>/<uuid>.jpg",
  "slot": "clear",
  "primary_image_slot": "clear",
  "image_slots": {
    "clear": "/media/..."
  },
  "location_id": "loc_A"
}
```

## 4.3. Unique file path per slot

Store files as:

```text
/media/locations/<context_id>/<location_id>/<slot>/<uuid>.<ext>
```

This also fixes stale browser cache bugs.

## 4.4. Delete slot image

Add endpoint or extend existing delete:

```text
DELETE /zone-stalkers/locations/{context_id}/{location_id}/image?slot=clear
```

Behavior:

```text
- delete only that slot image;
- set loc.image_slots[slot] = null;
- if deleted slot was primary, choose next available slot as primary;
- update loc.image_url;
- if no images remain, image_url = null, primary_image_slot = null.
```

Also support:

```text
DELETE .../image
```

without slot to delete all images, or keep legacy behavior as “delete primary image”. Recommended:

```text
DELETE without slot = delete all location images
DELETE with slot = delete specific slot
```

## 4.5. Set primary slot command

Add command:

```text
debug_set_location_primary_image
```

Payload:

```json
{
  "loc_id": "loc_A",
  "slot": "rain"
}
```

Validation:

```text
- loc exists;
- slot in allowed slots;
- slot has image URL.
```

Resolve:

```python
loc["primary_image_slot"] = slot
_sync_location_primary_image_url(loc)
```

Event:

```json
{
  "event_type": "debug_location_primary_image_set",
  "payload": {
    "loc_id": "loc_A",
    "slot": "rain"
  }
}
```

Alternative: use existing `debug_update_location` with `primary_image_slot`. A dedicated command is clearer and easier to wire to buttons.

---

# 5. Migration / backward compatibility

## 5.1. Existing locations with only `image_url`

On load/projection/tick or via one-time migration:

```python
def migrate_location_images(loc: dict) -> None:
    if "image_slots" not in loc:
        loc["image_slots"] = {}

    if loc.get("image_url") and not any(loc["image_slots"].values()):
        loc["image_slots"]["clear"] = loc["image_url"]
        loc["primary_image_slot"] = "clear"

    _sync_location_primary_image_url(loc)
```

Where to run:

```text
- in world generation for new locations;
- in debug_create_location;
- in projection helper;
- optionally in tick migration once per state.
```

## 5.2. New locations

`debug_create_location` should initialize:

```python
"image_slots": {
    "clear": None,
    "fog": None,
    "rain": None,
    "night_clear": None,
    "night_rain": None,
},
"primary_image_slot": None,
"image_url": None,
```

## 5.3. Generated locations

Update generator/fixed map creation to include the same fields or rely on migration helper.

---

# 6. Frontend UI changes

## 6.1. Debug map layout

Change layout target:

```text
Before:
  map large / detail narrow

After:
  map slightly narrower / detail wider
```

Suggested CSS:

```ts
debugMapPageLayout: {
  display: "grid",
  gridTemplateColumns: "minmax(520px, 1fr) minmax(420px, 520px)",
  gap: 12,
  minHeight: 0,
}
```

For fullscreen:

```ts
gridTemplateColumns: "minmax(600px, 1fr) minmax(460px, 560px)"
```

Detail panel:

```ts
detail: {
  minWidth: 420,
  maxWidth: 560,
  overflowY: "auto",
  overflowX: "hidden",
  minHeight: 0,
}
```

## 6.2. Prevent overflow inside detail panel

For all rows:

```ts
wordBreak: "break-word",
overflowWrap: "anywhere",
minWidth: 0,
```

For row layouts:

```ts
DetailRow:
  display: "flex";
  alignItems: "center";
  gap: 8;
  minWidth: 0;

main text span:
  minWidth: 0;
  flex: 1;
  overflow: "hidden";
  textOverflow: "ellipsis";
```

For sections with buttons:

```ts
display: "flex";
flexWrap: "wrap";
gap: 6;
```

## 6.3. Bigger image preview

Replace current image preview:

```tsx
<img
  style={{ width: '100%', maxHeight: 200 }}
/>
```

With:

```tsx
<img
  key={primaryImageUrl}
  src={primaryImageUrl}
  alt={loc.name}
  style={{
    width: "100%",
    maxHeight: 360,
    minHeight: 220,
    borderRadius: 8,
    objectFit: "cover",
    border: "1px solid #1e3a5f",
    background: "#020617",
  }}
/>
```

If image natural aspect is important, use:

```ts
objectFit: "contain"
```

But for location atmosphere, `cover` is probably better.

## 6.4. Image slots UI in LocationDetailPanel

Add section:

```tsx
<Section label="🖼 Изображения локации">
  <Primary image preview />
  <Slot buttons />
  <Upload/delete controls per slot />
</Section>
```

Slot row design:

```text
[☀️ Ясно] [🌫️ Туман] [🌧️ Дождь] [🌙 Ночь ясно] [🌧️🌙 Ночь дождь]
```

Each slot button shows state:

```text
- active primary: highlighted border;
- has image: normal/green indicator;
- empty: muted/dashed;
```

Example:

```tsx
{LOCATION_IMAGE_SLOTS.map((slot) => {
  const hasImage = Boolean(loc.image_slots?.[slot]);
  const isPrimary = loc.primary_image_slot === slot;

  return (
    <button
      key={slot}
      onClick={() => hasImage && onSetPrimaryImageSlot(slot)}
      disabled={!hasImage}
      title={hasImage ? "Сделать приоритетной" : "Сначала загрузите изображение"}
    >
      {isPrimary ? "★ " : ""}
      {LOCATION_IMAGE_SLOT_LABELS[slot]}
    </button>
  );
})}
```

## 6.5. Upload slot controls

MVP UI:

```text
- one <select> for slot;
- one Upload button;
- one Delete selected slot button;
- row of buttons to set primary.
```

Better final UI:

```text
Five compact cards, one per slot.
```

## 6.6. Required props

Extend `LocationDetailPanel` props:

```ts
onUploadLocationImageSlot?: (slot: LocationImageSlot, file: File) => Promise<void>;
onDeleteLocationImageSlot?: (slot: LocationImageSlot) => Promise<void>;
onSetPrimaryImageSlot?: (slot: LocationImageSlot) => Promise<void>;
```

Or keep upload in parent `DebugMapPage` and pass handlers.

---

# 7. Normal interface image selection

Wherever normal game UI displays a location image, use:

```ts
getPrimaryLocationImageUrl(loc)
```

Do not use `loc.image_url` directly, except as legacy fallback inside helper.

This ensures primary slot controls the visible image everywhere.

---

# 8. API client changes

In `frontend/src/api/client.ts`, update `locationsApi.uploadImage`.

Current likely shape:

```ts
uploadImage(contextId, locationId, file)
```

New shape:

```ts
uploadImage(contextId: string, locationId: string, file: File, slot?: LocationImageSlot)
```

Implementation:

```ts
const form = new FormData();
form.append("file", file);
if (slot) form.append("slot", slot);

return api.post(`/zone-stalkers/locations/${contextId}/${locationId}/image`, form);
```

If backend expects query:

```ts
return api.post(
  `/zone-stalkers/locations/${contextId}/${locationId}/image`,
  form,
  { params: { slot } },
);
```

Add:

```ts
deleteImage(contextId: string, locationId: string, slot?: LocationImageSlot)
setPrimaryImageSlot(contextId: string, locationId: string, slot: LocationImageSlot)
```

or use `sendCommand("debug_set_location_primary_image", ...)`.

---

# 9. Backend tests

Add tests:

```text
backend/tests/test_zone_stalkers_debug_spawn.py
backend/tests/test_zone_stalkers_location_images.py
```

## 9.1. Spawn tests

```python
def test_debug_spawn_stalker_location_and_agent_consistent():
    ...
```

Assert:

```text
new id in state.agents
new id in state.locations[loc_id].agents
agent.location_id == loc_id
agent has UI-required fields
```

## 9.2. Multi-image migration test

```python
def test_location_image_legacy_url_migrates_to_clear_slot():
    loc = {"image_url": "/media/old.jpg"}
    migrate_location_images(loc)
    assert loc["image_slots"]["clear"] == "/media/old.jpg"
    assert loc["primary_image_slot"] == "clear"
    assert loc["image_url"] == "/media/old.jpg"
```

## 9.3. Upload slot test

```python
def test_upload_location_image_slot_updates_correct_slot():
    res = upload(slot="rain")
    state = load_context_state(...)
    loc = state["locations"][loc_id]
    assert loc["image_slots"]["rain"] == res.json()["url"]
```

## 9.4. Primary slot test

```python
def test_set_primary_image_slot_updates_image_url():
    loc["image_slots"]["clear"] = "/clear.jpg"
    loc["image_slots"]["rain"] = "/rain.jpg"
    command("debug_set_location_primary_image", {"loc_id": loc_id, "slot": "rain"})
    assert loc["primary_image_slot"] == "rain"
    assert loc["image_url"] == "/rain.jpg"
```

## 9.5. Delete slot test

```python
def test_delete_primary_slot_falls_back_to_next_available():
    clear + rain exist
    primary = rain
    delete rain
    assert primary == clear
    assert image_url == clear_url
```

---

# 10. Frontend tests / manual checklist

## 10.1. Spawn crash

```text
[ ] Open debug map on main-like state.
[ ] Select a location.
[ ] Spawn one stalker.
[ ] UI does not crash.
[ ] New stalker appears in "Персонажи".
[ ] Click new stalker.
[ ] Agent profile modal opens.
[ ] Spawn 5 stalkers quickly.
[ ] UI still does not crash.
```

## 10.2. Image slots

```text
[ ] Select location.
[ ] Upload image to "Ясно".
[ ] It becomes primary if no primary existed.
[ ] Upload image to "Дождь".
[ ] Primary remains "Ясно" unless user changes it.
[ ] Click "Дождь" / "Сделать приоритетной".
[ ] Big preview changes to rain image.
[ ] Normal interface uses rain image.
[ ] Delete rain image.
[ ] Primary falls back to clear.
[ ] Upload all 5 slots.
[ ] Switch primary between all 5 without layout overflow.
```

## 10.3. Layout

```text
[ ] Detail panel is wider than before.
[ ] Map remains usable.
[ ] Image preview is large.
[ ] Long location names do not overflow.
[ ] Long ids/URLs do not overflow.
[ ] Buttons wrap instead of overflowing.
[ ] Fullscreen mode still works.
[ ] Narrow browser width still usable.
```

---

# 11. Suggested implementation order

1. Frontend hardening for `loc.agents` rendering.
2. Backend hardening for `debug_spawn_stalker`.
3. Tests/manual check for spawn crash.
4. Add image slot types/helpers on frontend.
5. Add backend image slot constants/helpers/migration.
6. Extend `debug_update_location` with `image_slots` and `primary_image_slot`.
7. Extend upload/delete image endpoints with `slot`.
8. Add `debug_set_location_primary_image` command.
9. Update API client.
10. Update `LocationDetailPanel` UI.
11. Update normal UI to use `getPrimaryLocationImageUrl`.
12. Adjust debug map layout widths and overflow styles.
13. Add tests and manual QA.

---

# 12. Acceptance criteria

## Spawn crash

```text
[ ] Spawning a stalker on any location no longer crashes frontend.
[ ] Unknown/stale occupant ids do not crash detail panel.
[ ] Backend spawn produces UI-safe agent objects.
[ ] Duplicate occupant ids are avoided.
```

## Multi-image locations

```text
[ ] Each location supports 5 image slots.
[ ] Each slot can be uploaded independently.
[ ] Each slot can be deleted independently.
[ ] Primary image slot can be switched from debug UI.
[ ] `image_url` reflects selected primary image for backward compatibility.
[ ] Existing single-image locations migrate to `clear` slot.
[ ] Normal UI and debug UI show primary image.
```

## UI layout

```text
[ ] Map is slightly narrower.
[ ] Detail panel is wider.
[ ] Detail content does not overflow horizontally.
[ ] Image preview is visibly larger.
[ ] Slot controls are usable and compact.
```

---

# 13. Important notes

## 13.1. Do not remove `image_url` immediately

Keep `image_url` for backward compatibility.

Code should treat it as derived/legacy:

```text
image_url = selected primary image URL
```

Later, after all UI migrates, it can be deprecated.

## 13.2. Prefer unique image URLs

When uploading images, do not overwrite the same path. Use UUID filenames:

```text
/media/locations/<context_id>/<location_id>/<slot>/<uuid>.<ext>
```

This avoids browser cache bugs.

## 13.3. UI should tolerate stale state

Even after backend fixes, frontend should not assume perfect consistency between:

```text
loc.agents
zoneState.agents
zoneState.traders
zoneState.mutants
```

Debug tools must be robust against partially edited/corrupt state.

---

# 14. Files likely touched

```text
backend/app/games/zone_stalkers/rules/world_rules.py
backend/app/games/zone_stalkers/router.py
backend/app/games/zone_stalkers/models.py
frontend/src/api/client.ts
frontend/src/games/zone_stalkers/ui/DebugMapPage.tsx
frontend/src/games/zone_stalkers/ui/debugMap/DetailPanels.tsx
frontend/src/games/zone_stalkers/ui/debugMap/types.ts
frontend/src/games/zone_stalkers/ui/debugMap/styles.ts
frontend/src/games/zone_stalkers/ui/debugMap/Modals.tsx
```

Tests:

```text
backend/tests/test_zone_stalkers_debug_spawn.py
backend/tests/test_zone_stalkers_location_images.py
```

Optional docs:

```text
docs/zone_stalkers_location_images.md
```
