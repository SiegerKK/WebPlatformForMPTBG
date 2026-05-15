# Zone Stalkers — PR plan: фикс floating bug изображений и новая система image slots для локаций

## Цель PR

Сделать два связанных изменения:

```text
1. Исправить текущий плавающий баг, при котором frontend иногда начинает думать,
   что у всех локаций есть только один слот "Ясно", хотя фактически слотов больше.

2. Переработать систему картинок локаций:
   - гарантированные базовые погодные слоты;
   - гарантированные мрачные погодные слоты;
   - опциональные аномальные слоты;
   - опциональные пси-слоты по степени воздействия;
   - отдельный набор слотов для подземных локаций, заменяющий обычные погодные наборы.
```

---

# 1. Текущий floating bug, который нужно закрыть

## 1.1. Симптом

Иногда в debug map / location details все локации начинают выглядеть так, будто у них есть только один слот:

```text
clear / Ясно
```

Но внутри этого слота может оказаться любая картинка:

```text
fog image
rain image
night image
night_rain image
```

Перезагрузка страницы лечит проблему.

## 1.2. Вероятная причина

Backend projection/delta не везде отдают полную slot-модель.

Опасная цепочка:

```text
1. В backend state у локации есть image_slots и primary_image_slot.
2. Какой-то projection/refresh отдаёт только image_url.
3. Frontend получает loc без image_slots.
4. Frontend legacy fallback считает:
   "если image_slots нет, но есть image_url — значит это старый single-image формат".
5. image_url записывается в clear.
6. Все остальные слоты выглядят пустыми.
```

Так как `image_url` сейчас является derived primary image URL, в `clear` может попасть любая текущая primary-картинка.

## 1.3. Обязательный фикс

Нельзя больше допускать, чтобы актуальная slot-aware локация попадала на frontend только с `image_url`.

Нужно:

```text
[ ] Все backend projections должны отдавать image_slots / primary_image_slot.
[ ] Zone delta должен отслеживать image_slots / primary_image_slot / image_url.
[ ] Frontend не должен превращать incomplete projection в persistent clear-only local override.
[ ] Local optimistic image state должен merge-иться по слотам, а не заменять весь объект.
```

---

# 2. Новая модель изображений локации

## 2.1. Типы локаций

Каждая локация может иметь флаги:

```text
is_anomalous
is_psi
is_underground
```

Флаги управляют тем, какие группы image slots доступны в UI.

## 2.2. Группы слотов

### 2.2.1. Гарантированная базовая погода

Для всех обычных наземных локаций:

```text
normal.clear
normal.fog
normal.rain
normal.night_clear
normal.night_rain
```

### 2.2.2. Гарантированная мрачная погода

Для всех обычных наземных локаций:

```text
gloom.clear
gloom.fog
gloom.rain
gloom.night_clear
gloom.night_rain
```

Смысл: более мрачный/опасный/депрессивный вариант той же погодной сцены.

### 2.2.3. Опциональные аномальные слоты

Если `is_anomalous = true`:

```text
anomaly.clear
anomaly.fog
anomaly.rain
anomaly.night_clear
anomaly.night_rain
```

Смысл: та же погода, но с визуальными признаками аномальной активности.

### 2.2.4. Опциональные пси-слоты

Если `is_psi = true`:

```text
psi.low
psi.medium
psi.high
psi.critical
psi.max
```

Смысл: не погода, а состояние восприятия зрителя под воздействием пси-излучения.

Уровни:

```text
low       — лёгкое искажение, хроматическая аберрация, тревожность
medium    — заметное двоение, шум, периферические галлюцинации
high      — сильное психическое давление, warped perspective
critical  — почти срыв восприятия
max       — экстремальный эффект, реальность распадается
```

### 2.2.5. Подземные слоты

Если `is_underground = true`, обычные гарантированные группы `normal` + `gloom` заменяются на 10 подземных слотов.

Подземная локация НЕ обязана иметь:

```text
normal.*
gloom.*
```

Вместо этого она имеет:

```text
underground.default
underground.dark
underground.emergency_light
underground.power_failure
underground.flooded
underground.toxic
underground.anomaly
underground.psi_low
underground.psi_high
underground.combat
```

Рекомендуемая логика:

```text
- если is_underground=true, UI показывает underground group;
- если дополнительно is_psi=true, можно также показать psi group как advanced;
- если дополнительно is_anomalous=true, можно также показать anomaly group как advanced;
- базовая required-гарантия для underground — именно underground 10 slots.
```

---

# 3. Почему лучше перейти от flat slots к grouped slots

Сейчас слоты выглядят примерно так:

```json
{
  "clear": "/media/...",
  "fog": "/media/...",
  "rain": "/media/...",
  "night_clear": "/media/...",
  "night_rain": "/media/..."
}
```

Новая модель должна поддерживать 10–25+ слотов. Flat keys возможны:

```text
normal_clear
gloom_clear
anomaly_clear
psi_low
underground_default
```

Но лучше сделать grouped schema:

```json
{
  "normal": {
    "clear": null,
    "fog": null,
    "rain": null,
    "night_clear": null,
    "night_rain": null
  },
  "gloom": {
    "clear": null,
    "fog": null,
    "rain": null,
    "night_clear": null,
    "night_rain": null
  },
  "anomaly": {
    "clear": null,
    "fog": null,
    "rain": null,
    "night_clear": null,
    "night_rain": null
  },
  "psi": {
    "low": null,
    "medium": null,
    "high": null,
    "critical": null,
    "max": null
  },
  "underground": {
    "default": null,
    "dark": null,
    "emergency_light": null,
    "power_failure": null,
    "flooded": null,
    "toxic": null,
    "anomaly": null,
    "psi_low": null,
    "psi_high": null,
    "combat": null
  }
}
```

Плюсы grouped schema:

```text
- проще отображать UI группами;
- проще включать/выключать группы по флагам локации;
- проще валидировать slot path;
- проще расширять;
- меньше риска спутать weather-slot и psi-slot;
- удобнее писать selection logic.
```

---

# 4. Новые поля локации

## 4.1. Backend state

В `state.locations[loc_id]` добавить/стандартизировать:

```json
{
  "image_profile": {
    "is_anomalous": false,
    "is_psi": false,
    "is_underground": false
  },
  "image_slots_v2": {
    "normal": {},
    "gloom": {},
    "anomaly": {},
    "psi": {},
    "underground": {}
  },
  "primary_image_ref": {
    "group": "normal",
    "slot": "clear"
  },

  "image_slots": {},
  "primary_image_slot": "clear",
  "image_url": "/media/..."
}
```

Новая каноничная модель:

```text
image_profile
image_slots_v2
primary_image_ref
```

Legacy model:

```text
image_slots
primary_image_slot
image_url
```

`image_url` должен остаться как derived compatibility/display field.

## 4.2. TypeScript типы

```ts
export type WeatherSlot =
  | 'clear'
  | 'fog'
  | 'rain'
  | 'night_clear'
  | 'night_rain';

export type PsiSlot =
  | 'low'
  | 'medium'
  | 'high'
  | 'critical'
  | 'max';

export type UndergroundSlot =
  | 'default'
  | 'dark'
  | 'emergency_light'
  | 'power_failure'
  | 'flooded'
  | 'toxic'
  | 'anomaly'
  | 'psi_low'
  | 'psi_high'
  | 'combat';

export type LocationImageGroup =
  | 'normal'
  | 'gloom'
  | 'anomaly'
  | 'psi'
  | 'underground';

export type LocationImageRef = {
  group: LocationImageGroup;
  slot: string;
};

export type LocationImageSlotsV2 = {
  normal?: Partial<Record<WeatherSlot, string | null>>;
  gloom?: Partial<Record<WeatherSlot, string | null>>;
  anomaly?: Partial<Record<WeatherSlot, string | null>>;
  psi?: Partial<Record<PsiSlot, string | null>>;
  underground?: Partial<Record<UndergroundSlot, string | null>>;
};

export type LocationImageProfile = {
  is_anomalous?: boolean;
  is_psi?: boolean;
  is_underground?: boolean;
};
```

---

# 5. Slot definitions as single source of truth

## 5.1. Backend

Создать/расширить:

```text
backend/app/games/zone_stalkers/location_images.py
```

Добавить:

```python
WEATHER_IMAGE_SLOTS = ("clear", "fog", "rain", "night_clear", "night_rain")
PSI_IMAGE_SLOTS = ("low", "medium", "high", "critical", "max")
UNDERGROUND_IMAGE_SLOTS = (
    "default",
    "dark",
    "emergency_light",
    "power_failure",
    "flooded",
    "toxic",
    "anomaly",
    "psi_low",
    "psi_high",
    "combat",
)

LOCATION_IMAGE_GROUPS = ("normal", "gloom", "anomaly", "psi", "underground")

LOCATION_IMAGE_GROUP_SLOT_MAP = {
    "normal": WEATHER_IMAGE_SLOTS,
    "gloom": WEATHER_IMAGE_SLOTS,
    "anomaly": WEATHER_IMAGE_SLOTS,
    "psi": PSI_IMAGE_SLOTS,
    "underground": UNDERGROUND_IMAGE_SLOTS,
}
```

## 5.2. Frontend

В:

```text
frontend/src/games/zone_stalkers/ui/debugMap/types.ts
```

добавить аналогичные constants:

```ts
export const WEATHER_IMAGE_SLOTS = ['clear', 'fog', 'rain', 'night_clear', 'night_rain'] as const;
export const PSI_IMAGE_SLOTS = ['low', 'medium', 'high', 'critical', 'max'] as const;
export const UNDERGROUND_IMAGE_SLOTS = [
  'default',
  'dark',
  'emergency_light',
  'power_failure',
  'flooded',
  'toxic',
  'anomaly',
  'psi_low',
  'psi_high',
  'combat',
] as const;

export const LOCATION_IMAGE_GROUPS = ['normal', 'gloom', 'anomaly', 'psi', 'underground'] as const;
```

---

# 6. Какие группы показывать для локации

## 6.1. Active groups

Helper:

```ts
export function getEnabledImageGroups(profile?: LocationImageProfile): LocationImageGroup[] {
  if (profile?.is_underground) {
    const groups: LocationImageGroup[] = ['underground'];

    // Optional advanced overlays
    if (profile?.is_anomalous) groups.push('anomaly');
    if (profile?.is_psi) groups.push('psi');

    return groups;
  }

  const groups: LocationImageGroup[] = ['normal', 'gloom'];

  if (profile?.is_anomalous) groups.push('anomaly');
  if (profile?.is_psi) groups.push('psi');

  return groups;
}
```

## 6.2. Required groups

Helper:

```ts
export function getRequiredImageGroups(profile?: LocationImageProfile): LocationImageGroup[] {
  if (profile?.is_underground) return ['underground'];
  return ['normal', 'gloom'];
}
```

---

# 7. Primary image selection

## 7.1. Новый primary ref

Вместо:

```json
"primary_image_slot": "rain"
```

использовать:

```json
"primary_image_ref": {
  "group": "gloom",
  "slot": "rain"
}
```

## 7.2. Fallback priority

Если primary ref отсутствует или указывает на пустой слот.

Для наземной локации:

```text
1. normal.clear
2. gloom.clear
3. normal.fog
4. normal.rain
5. normal.night_clear
6. normal.night_rain
7. gloom.fog
8. gloom.rain
9. gloom.night_clear
10. gloom.night_rain
11. anomaly.* если enabled
12. psi.low если enabled
```

Для подземной:

```text
1. underground.default
2. underground.dark
3. underground.emergency_light
4. underground.power_failure
5. underground.flooded
6. underground.toxic
7. underground.anomaly
8. underground.psi_low
9. underground.psi_high
10. underground.combat
11. psi.low если enabled
12. anomaly.clear если enabled
```

## 7.3. Backend helper

```python
def get_location_primary_image_url(loc: dict) -> str | None:
    migrate_location_images_v2(loc)

    ref = loc.get("primary_image_ref")
    slots = loc.get("image_slots_v2") or {}

    if isinstance(ref, dict):
        group = ref.get("group")
        slot = ref.get("slot")
        url = ((slots.get(group) or {}).get(slot))
        if url:
            return url

    for group, slot in get_fallback_image_refs(loc):
        url = ((slots.get(group) or {}).get(slot))
        if url:
            loc["primary_image_ref"] = {"group": group, "slot": slot}
            return url

    return loc.get("image_url")
```

## 7.4. Frontend helper

```ts
export function getPrimaryLocationImageUrlV2(loc: ZoneLocation): string | null {
  const slots = normalizeImageSlotsV2(loc);
  const ref = loc.primary_image_ref;

  if (ref) {
    const url = getImageSlotUrl(slots, ref);
    if (url) return url;
  }

  for (const fallbackRef of getFallbackImageRefs(loc)) {
    const url = getImageSlotUrl(slots, fallbackRef);
    if (url) return url;
  }

  return loc.image_url ?? null;
}
```

---

# 8. Migration

## 8.1. Legacy migration from current 5 slots

Current:

```json
{
  "image_slots": {
    "clear": "...",
    "fog": "...",
    "rain": "...",
    "night_clear": "...",
    "night_rain": "..."
  },
  "primary_image_slot": "rain",
  "image_url": "..."
}
```

Migrate to:

```json
{
  "image_slots_v2": {
    "normal": {
      "clear": "...",
      "fog": "...",
      "rain": "...",
      "night_clear": "...",
      "night_rain": "..."
    },
    "gloom": {
      "clear": null,
      "fog": null,
      "rain": null,
      "night_clear": null,
      "night_rain": null
    }
  },
  "primary_image_ref": {
    "group": "normal",
    "slot": "rain"
  }
}
```

If only legacy `image_url` exists:

```json
{
  "image_slots_v2": {
    "normal": {
      "clear": "<image_url>"
    }
  },
  "primary_image_ref": {
    "group": "normal",
    "slot": "clear"
  }
}
```

## 8.2. Migration helper

```python
def migrate_location_images_v2(loc: dict) -> None:
    profile = loc.setdefault("image_profile", {})
    profile.setdefault("is_anomalous", False)
    profile.setdefault("is_psi", False)
    profile.setdefault("is_underground", False)

    slots_v2 = loc.setdefault("image_slots_v2", {})

    # Ensure group skeletons
    for group in enabled_or_required_groups(profile):
        slots_v2.setdefault(group, {})
        for slot in LOCATION_IMAGE_GROUP_SLOT_MAP[group]:
            slots_v2[group].setdefault(slot, None)

    # Migrate old image_slots into normal.*
    old_slots = loc.get("image_slots")
    if isinstance(old_slots, dict):
        normal = slots_v2.setdefault("normal", {})
        for slot in WEATHER_IMAGE_SLOTS:
            if old_slots.get(slot) and not normal.get(slot):
                normal[slot] = old_slots.get(slot)

    # Migrate old primary_image_slot
    old_primary = loc.get("primary_image_slot")
    if old_primary and not loc.get("primary_image_ref"):
        loc["primary_image_ref"] = {"group": "normal", "slot": old_primary}

    # Migrate old image_url
    if loc.get("image_url") and not any_image_in_slots_v2(slots_v2):
        slots_v2.setdefault("normal", {})["clear"] = loc.get("image_url")
        loc.setdefault("primary_image_ref", {"group": "normal", "slot": "clear"})

    sync_location_primary_image_url_v2(loc)
```

Important:

```text
Projection helpers may call this on a copy.
Mutation helpers may call this on real state.
```

---

# 9. DB model update

Current `LocationImage` has:

```text
context_id
location_id
slot
filename
content_type
file_path
```

Recommended: add `group` column.

```python
group = Column(String, nullable=False, default="normal")
slot = Column(String, nullable=False)
```

Unique constraint:

```text
(context_id, location_id, group, slot)
```

Migration:

```text
existing rows:
  group = "normal"
  slot = old slot
```

## 9.1. Upload path

Old path:

```text
locations/<context_id>/<location_id>/<slot>/<uuid>.<ext>
```

New path:

```text
locations/<context_id>/<location_id>/<group>/<slot>/<uuid>.<ext>
```

Example:

```text
locations/<context_id>/loc_A/gloom/rain/abc.webp
```

---

# 10. Backend API changes

## 10.1. Upload image slot

Current:

```text
POST /locations/{context_id}/{location_id}/image
form: file, slot
```

New:

```text
POST /locations/{context_id}/{location_id}/image
form:
  file
  group
  slot
```

Defaults for backward compatibility:

```text
group = "normal"
slot = "clear"
```

Validation:

```python
validate_image_group_slot(group, slot)
validate_group_enabled_for_location(loc, group)
```

If group is optional but flag disabled:

```text
return 400 with explanation
```

Recommended for first PR:

```text
Do not auto-enable. Return 400.
Frontend should enable the checkbox first.
```

## 10.2. Delete image slot

Current:

```text
DELETE /locations/{context_id}/{location_id}/image?slot=clear
```

New:

```text
DELETE /locations/{context_id}/{location_id}/image?group=normal&slot=clear
```

Without group/slot:

```text
delete all images for location
```

With group only:

```text
delete all images in group
```

With group+slot:

```text
delete one slot
```

## 10.3. Set primary image

If not already present, add endpoint:

```text
POST /locations/{context_id}/{location_id}/image/primary
```

Payload:

```json
{
  "group": "gloom",
  "slot": "rain"
}
```

Response:

```json
{
  "location_id": "loc_A",
  "primary_image_ref": {
    "group": "gloom",
    "slot": "rain"
  },
  "image_url": "/media/...",
  "image_slots_v2": {},
  "state_revision": 123,
  "map_revision": 45
}
```

## 10.4. Update image profile

Add endpoint or include in existing debug update location command:

```text
PATCH /locations/{context_id}/{location_id}/image-profile
```

Payload:

```json
{
  "is_anomalous": true,
  "is_psi": false,
  "is_underground": false
}
```

Behavior:

```text
- update image_profile;
- ensure required slot skeletons;
- if disabling optional group, do NOT delete images automatically;
- hide disabled optional group in UI;
- if current primary_image_ref points to disabled group, choose fallback primary;
- sync image_url;
- increment map_revision/state_revision.
```

Important:

```text
Disabling is_anomalous or is_psi must not delete images.
It should only hide/disable the group.
```

---

# 11. Projection and delta fixes

This PR must fix the current bug.

## 11.1. Projection

Every projection that includes locations must include:

```python
"image_profile": loc.get("image_profile"),
"image_slots_v2": loc.get("image_slots_v2"),
"primary_image_ref": loc.get("primary_image_ref"),

# legacy compatibility:
"image_url": loc.get("image_url"),
"image_slots": loc.get("image_slots"),
"primary_image_slot": loc.get("primary_image_slot"),
```

At minimum:

```text
game
debug-map
debug-map-lite
zone-lite if it includes locations
map-static
full
```

## 11.2. Delta

Add image fields to location hot fields:

```python
_LOCATION_HOT_FIELDS = frozenset({
    "agents",
    "artifacts",
    "items",
    "anomaly_activity",
    "dominant_anomaly_type",

    "image_profile",
    "image_slots_v2",
    "primary_image_ref",

    "image_url",
    "image_slots",
    "primary_image_slot",
})
```

And to compact location delta:

```python
"image_profile": location.get("image_profile"),
"image_slots_v2": location.get("image_slots_v2"),
"primary_image_ref": location.get("primary_image_ref"),
"image_url": location.get("image_url"),
"image_slots": location.get("image_slots"),
"primary_image_slot": location.get("primary_image_slot"),
```

---

# 12. Frontend changes

## 12.1. Detail panel

Current detail panel shows:

```text
primary preview
buttons for 5 weather slots
```

New detail panel should show:

```text
primary preview
enabled group tabs/sections
buttons for available slots
```

Example:

```text
🖼 Изображения локации

Primary:
[large image preview]

Profile:
[ ] Аномальная
[ ] Пси
[ ] Подземная

Groups:
Normal weather
  [☀ clear] [🌫 fog] [🌧 rain] [🌙 night_clear] [🌧🌙 night_rain]

Gloom weather
  [☀ clear] [🌫 fog] [🌧 rain] [🌙 night_clear] [🌧🌙 night_rain]

Anomaly weather
  visible only if is_anomalous

Psi radiation
  visible only if is_psi

Underground
  visible only if is_underground
```

In debug map detail panel:

```text
- can choose primary image;
- can toggle profile flags;
- should not upload images there if edit modal owns upload.
```

## 12.2. Edit location modal

Edit modal should own uploads.

For each enabled group:

```text
group title
slot cards
[preview]
[upload]
[download]
[delete]
[make primary]
```

For disabled optional groups:

```text
show collapsed hint:
"Enable 'Пси' in location profile to manage psi image slots"
```

For underground:

```text
if is_underground=true:
  show underground 10 slots prominently;
  normal/gloom groups hidden or marked replaced.
```

## 12.3. Slot card design

Each slot card:

```text
label
thumbnail
status:
  empty / filled / primary
actions:
  upload
  download
  delete
  make primary
```

For thumbnails:

```text
object-fit: contain
background: #020617
```

Large primary preview:

```text
width: 100%
height: auto
object-fit: contain
max-height: 60vh
```

---

# 13. Frontend state rules

## 13.1. No dangerous legacy fallback in state normalization

Do not use fallback:

```ts
if no slots and image_url:
  clear = image_url
```

inside local state normalization.

Instead:

```text
- strict normalization returns empty slots if image_slots_v2 absent;
- display helper may show legacy image_url only for display;
- local override never persists legacy fallback as clear.
```

## 13.2. Local overrides should be partial

Instead of storing full slot matrix:

```ts
localImageSlotsByLoc[locId] = fullImageSlots
```

store only changed refs:

```ts
type LocalImageSlotOverrides = Record<
  string,
  Partial<Record<LocationImageGroup, Partial<Record<string, string | null>>>>
>;
```

Merge:

```ts
effectiveSlots = deepMerge(serverSlots, localOverrides)
```

This prevents a stale partial response from erasing other slots.

## 13.3. Clear local overrides when backend catches up

After upload/delete/set-primary/profile update:

```text
- optimistic local override can be set;
- when zoneState contains same value, remove override;
- if backend returns authoritative image_slots_v2, prefer backend.
```

---

# 14. Image selection logic for gameplay

Add helper:

```python
def select_location_image_for_conditions(
    loc: dict,
    *,
    weather: str,
    is_night: bool,
    mood: str | None = None,
    anomaly_active: bool = False,
    psi_level: str | None = None,
    underground_condition: str | None = None,
) -> str | None:
    ...
```

## 14.1. Weather slot resolution

```python
def weather_to_slot(weather: str, is_night: bool) -> str:
    if is_night and weather == "rain":
        return "night_rain"
    if is_night:
        return "night_clear"
    if weather == "fog":
        return "fog"
    if weather == "rain":
        return "rain"
    return "clear"
```

## 14.2. Priority

For underground:

```text
1. specific underground condition
2. underground.default
3. primary_image_ref
4. any available image
```

For psi:

```text
1. psi.<level>
2. if level missing, nearest lower psi level
3. anomaly/weather if active
4. gloom/weather
5. normal/weather
6. primary_image_ref
```

For anomaly:

```text
1. anomaly/weather_slot
2. gloom/weather_slot
3. normal/weather_slot
```

For normal:

```text
1. gloom/weather_slot if mood=gloom
2. normal/weather_slot
3. primary_image_ref
```

This helper can later be used by runtime UI, generated event scenes, or location viewer.

---

# 15. Import/export

## 15.1. Export map

Export must include:

```text
image_profile
image_slots_v2
primary_image_ref

legacy:
image_slots
primary_image_slot
image_url
```

Also include actual media files for all v2 slots.

ZIP path example:

```text
locations/<location_id>/images/normal/clear.webp
locations/<location_id>/images/gloom/rain.webp
locations/<location_id>/images/psi/high.webp
```

Manifest:

```json
{
  "locations": {
    "loc_A": {
      "image_profile": {},
      "primary_image_ref": {},
      "images": {
        "normal": {
          "clear": "locations/loc_A/images/normal/clear.webp"
        }
      }
    }
  }
}
```

## 15.2. Import map

Import must:

```text
- restore image_profile;
- restore image_slots_v2;
- restore primary_image_ref;
- create LocationImage DB rows with group+slot;
- copy files to media storage;
- sync image_url from primary ref;
- migrate legacy old exports.
```

## 15.3. Backward compatibility

If imported map only has old `image_slots`:

```text
migrate old slots to image_slots_v2.normal
```

If imported map only has `image_url`:

```text
migrate to image_slots_v2.normal.clear
```

---

# 16. Bulk optimization compatibility

Bulk image optimizer must process v2 slots.

Search source images from:

```text
LocationImage rows grouped by context_id/location_id/group/slot
```

and update:

```text
LocationImage.file_path
LocationImage.content_type
LocationImage.filename
state.locations[loc_id].image_slots_v2[group][slot]
state.locations[loc_id].image_url if primary points to this group+slot
```

Legacy `image_slots` should be updated only for compatibility if group is `normal`.

---

# 17. Backend tests

## 17.1. Migration tests

```text
[ ] legacy image_url migrates to image_slots_v2.normal.clear.
[ ] legacy image_slots migrate to image_slots_v2.normal.*
[ ] legacy primary_image_slot migrates to primary_image_ref normal.<slot>.
[ ] new location gets required groups depending on is_underground.
[ ] disabling is_psi does not delete psi images.
[ ] disabling is_anomalous does not delete anomaly images.
[ ] enabling is_underground switches required groups to underground.
```

## 17.2. Upload/delete tests

```text
[ ] upload normal.clear stores DB group=normal slot=clear.
[ ] upload gloom.rain stores DB group=gloom slot=rain.
[ ] upload psi.high rejected if is_psi=false.
[ ] upload psi.high accepted if is_psi=true.
[ ] upload anomaly.rain rejected if is_anomalous=false.
[ ] upload underground.dark accepted if is_underground=true.
[ ] delete one slot clears only that group+slot.
[ ] delete group clears all slots in group.
[ ] delete all clears all image groups.
```

## 17.3. Primary tests

```text
[ ] set primary to gloom.rain updates primary_image_ref.
[ ] image_url syncs to gloom.rain URL.
[ ] deleting primary slot selects fallback.
[ ] disabling group used by primary selects fallback but does not delete image.
```

## 17.4. Projection/delta tests

```text
[ ] game projection includes image_slots_v2 and primary_image_ref.
[ ] debug-map projection includes image_slots_v2 and primary_image_ref.
[ ] map-static includes image_slots_v2 and primary_image_ref.
[ ] zone delta includes image_slots_v2 changes.
[ ] zone delta includes primary_image_ref changes.
```

## 17.5. Import/export tests

```text
[ ] export includes all v2 images.
[ ] import restores all v2 images.
[ ] old export with image_slots imports into normal group.
[ ] old export with image_url imports into normal.clear.
```

---

# 18. Frontend tests

## 18.1. Slot helpers

```text
[ ] getEnabledImageGroups for normal location => normal,gloom.
[ ] getEnabledImageGroups for anomalous => normal,gloom,anomaly.
[ ] getEnabledImageGroups for psi => normal,gloom,psi.
[ ] getEnabledImageGroups for underground => underground.
[ ] getEnabledImageGroups for underground+psi => underground,psi.
[ ] getPrimaryLocationImageUrlV2 respects primary_image_ref.
[ ] getPrimaryLocationImageUrlV2 falls back predictably.
[ ] strict normalization does not convert image_url to clear.
```

## 18.2. UI

```text
[ ] normal location shows normal+gloom groups.
[ ] anomalous checkbox shows anomaly group.
[ ] psi checkbox shows psi group.
[ ] underground checkbox hides/replaces normal+gloom with underground group.
[ ] upload updates only selected group+slot.
[ ] local override does not erase other slots.
[ ] selecting primary updates only preview image, not full panel remount.
```

---

# 19. Manual QA checklist

```text
[ ] Open existing map with old 5-slot images.
[ ] Existing images appear under normal group.
[ ] Primary image remains correct.
[ ] Toggle anomalous: anomaly group appears.
[ ] Upload anomaly.rain.
[ ] Set anomaly.rain as primary.
[ ] Reload page: primary remains anomaly.rain.
[ ] Toggle psi: psi group appears.
[ ] Upload psi.high.
[ ] Set psi.high as primary.
[ ] Reload page: all groups remain intact.
[ ] Toggle underground: underground slots appear.
[ ] Upload underground.default.
[ ] Set underground.default as primary.
[ ] Disable underground: normal/gloom groups return, underground files not deleted.
[ ] Auto-run / WebSocket updates do not collapse slots to clear.
[ ] Import/export preserves all image groups.
```

---

# 20. PR implementation order

## Step 1 — Fix current floating bug

```text
[ ] Add image_slots / primary_image_slot to all existing projections.
[ ] Add image image fields to zone delta.
[ ] Remove dangerous frontend fallback from state normalization.
[ ] Make local image overrides partial and deep-merged.
[ ] Add projection/delta/frontend tests for current 5-slot model.
```

This should be committed first inside the PR or split into a smaller PR.

## Step 2 — Add v2 schema helpers

```text
[ ] Add backend slot definitions.
[ ] Add frontend slot definitions.
[ ] Add migration helpers.
[ ] Add primary image helper.
```

## Step 3 — DB migration

```text
[ ] Add LocationImage.group column.
[ ] Add unique constraint context_id/location_id/group/slot.
[ ] Migrate existing rows group=normal.
```

## Step 4 — Update APIs

```text
[ ] Upload accepts group+slot.
[ ] Delete accepts group+slot.
[ ] Set primary accepts group+slot.
[ ] Update image profile endpoint.
```

## Step 5 — Update frontend UI

```text
[ ] New grouped slot UI in edit modal.
[ ] New primary selector in detail panel.
[ ] Profile checkboxes.
[ ] Underground replacement logic.
```

## Step 6 — Import/export

```text
[ ] Export v2 image manifest.
[ ] Import v2 image manifest.
[ ] Backward compatibility with old exports.
```

## Step 7 — Tests and QA

```text
[ ] Backend tests.
[ ] Frontend tests.
[ ] Manual QA.
```

---

# 21. Acceptance criteria

```text
[ ] Floating bug with "all images become clear" is fixed.
[ ] Every normal location has normal+gloom slot groups.
[ ] Every underground location has underground 10-slot group instead of required normal+gloom.
[ ] Anomalous flag enables anomaly weather group.
[ ] Psi flag enables psi intensity group.
[ ] Images can be uploaded, deleted, downloaded and set as primary per group+slot.
[ ] Primary image is represented by primary_image_ref.
[ ] image_url remains a synced legacy/display field.
[ ] Backend projections include full image state.
[ ] WebSocket deltas include image state changes.
[ ] Frontend local optimistic state cannot erase other slots.
[ ] Import/export preserves all image groups and files.
[ ] Old maps with legacy image_slots continue to work.
```

---

# 22. Notes for Copilot

When implementing, do not treat `image_url` as authoritative slot state.

`image_url` should become:

```text
derived display compatibility field
```

Canonical source of truth:

```text
image_slots_v2 + primary_image_ref
```

Legacy fallback is allowed only for migration/display, not for writing local frontend state.

The most important bug-prevention rule:

```text
Never convert a partial projection containing only image_url into a full slot state.
```
