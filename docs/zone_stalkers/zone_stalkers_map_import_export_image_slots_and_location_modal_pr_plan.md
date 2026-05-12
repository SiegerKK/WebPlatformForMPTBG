# Zone Stalkers — PR plan: fix map import/export for image slots and move location image editing into LocationModal

## Назначение PR

После перехода локаций с одного поля `image_url` на новую схему:

```text
image_slots
primary_image_slot
image_url
```

сломались или стали некорректными:

1. экспорт карты;
2. импорт карты;
3. UI редактирования изображений локации;
4. поведение detail panel на debug-карте при переключении приоритетной картинки.

Этот PR должен исправить эти места.

---

# 1. Текущее состояние и проблема

## 1.1. Новая модель изображений

Canonical state локации:

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

`image_url` остаётся backward-compatible derived field:

```text
image_url = image_slots[primary_image_slot]
```

или fallback на первый доступный слот.

## 1.2. Что сломалось

Экспорт/импорт карты, судя по симптомам, всё ещё частично работает как будто у локации есть только одна картинка:

```text
location.image_url
```

После multi-slot схемы этого недостаточно.

Теперь экспорт должен сохранять:

```text
- image_slots;
- primary_image_slot;
- image_url as derived compatibility field;
- файлы изображений для всех заполненных slots.
```

Импорт должен восстанавливать:

```text
- все slot-картинки;
- primary_image_slot;
- image_url;
- map state;
- media files or uploaded media references.
```

## 1.3. UI сейчас тоже в промежуточном состоянии

В `LocationDetailPanel` уже есть slot UI:

```text
- выпадающий список slot;
- upload;
- delete;
- set primary.
```

Но по новой задаче это должно быть иначе:

```text
Debug location detail panel:
  можно только выбрать primary image slot.

Location edit modal:
  можно загружать/удалять/скачивать изображения по всем 5 слотам.
```

В `LocationModal` пока осталась старая single-image модель:

```ts
imageFile?: File | null
initialImageUrl?: string | null
previewUrl
один input type=file
одна кнопка удалить изображение
```

Это надо заменить на полноценную multi-slot editing UI.

---

# 2. Целевое поведение

## 2.1. Detail panel на debug-карте

При выборе локации на карте правое меню просмотра локации должно показывать:

```text
- большую primary-картинку;
- 5 кнопок слотов;
- у каждого слота видно, есть картинка или нет;
- можно выбрать primary slot;
- нельзя загружать/удалять картинки;
- нельзя скачивать картинки отсюда.
```

То есть detail panel — это просмотр и быстрый выбор приоритетной картинки.

## 2.2. Location edit modal

Окно редактирования локации должно содержать полноценный редактор изображений:

```text
5 slot cards подряд:
  - Ясно
  - Туман
  - Дождь
  - Ночь ясно
  - Ночь дождь
```

Для каждого слота:

```text
- preview текущей картинки, если есть;
- кнопка "Загрузить";
- кнопка "Скачать картинку", если есть картинка;
- кнопка "Удалить", если есть картинка;
- индикатор, является ли slot primary;
- кнопка "Сделать приоритетной", если есть картинка и slot не primary.
```

Важно: без выпадающего списка. Все 5 слотов видны сразу.

## 2.3. Ререндер при выборе primary

Сейчас при выборе другой приоритетной картинки происходит ощущение полной перезагрузки всего меню просмотра локации.

Нужно сделать так:

```text
при переключении primary slot меняется только preview-картинка и активный state slot-кнопок,
остальной detail panel не должен пересоздаваться/мигать/сбрасывать scroll/состояния.
```

---

# 3. Fix import/export map

## 3.1. Найти текущую реализацию

Основные места для проверки:

```text
frontend/src/games/zone_stalkers/ui/DebugMapPage.tsx
backend/app/games/zone_stalkers/rules/world_rules.py
```

В проекте используется `JSZip` в `DebugMapPage.tsx`, значит экспорт/импорт, вероятно, живёт именно там.

Нужно найти функции/обработчики по смыслу:

```text
handleExport
handleImport
debug_import_full_map
JSZip
importInputRef
Blob
URL.createObjectURL
download
```

Если search плохо находит эти функции из-за объёма файла, открыть файл локально и искать по `JSZip`, `debug_import_full_map`, `importInputRef`, `download`.

## 3.2. Новый формат экспорта

Рекомендуемый формат ZIP:

```text
zone_map_export.zip
  map.json
  images/
    <location_id>/
      clear.<ext>
      fog.<ext>
      rain.<ext>
      night_clear.<ext>
      night_rain.<ext>
```

## 3.3. `map.json` schema

Добавить версию формата:

```json
{
  "schema_version": 2,
  "exported_at": "2026-...",
  "locations": {
    "loc_A": {
      "id": "loc_A",
      "name": "КПП",
      "terrain_type": "military_buildings",
      "region": "cordon",
      "exit_zone": false,
      "connections": [],
      "debug_layout": {},
      "image_slots": {
        "clear": "images/loc_A/clear.jpg",
        "fog": null,
        "rain": "images/loc_A/rain.webp",
        "night_clear": null,
        "night_rain": null
      },
      "primary_image_slot": "rain",
      "image_url": "images/loc_A/rain.webp"
    }
  },
  "debug_layout": {
    "positions": {},
    "regions": {}
  }
}
```

В export JSON не нужно сохранять абсолютные `/media/...` URLs как canonical image reference. Лучше сохранять относительный путь внутри ZIP.

## 3.4. Экспорт всех image slots

Для каждой локации:

```ts
for (const slot of LOCATION_IMAGE_SLOTS) {
  const url = loc.image_slots?.[slot];
  if (!url) continue;

  const blob = await fetch(url).then(r => r.blob());
  const ext = extensionFromUrlOrBlob(url, blob.type);
  const zipPath = `images/${loc.id}/${slot}${ext}`;

  zip.file(zipPath, blob);
  exportedLoc.image_slots[slot] = zipPath;

  if (loc.primary_image_slot === slot) {
    exportedLoc.image_url = zipPath;
  }
}
```

Если старый map имеет только `image_url` и нет `image_slots`:

```ts
const legacyUrl = loc.image_url;
if (legacyUrl && !hasAnySlot(loc.image_slots)) {
  export it as images/<loc_id>/clear.<ext>
  exportedLoc.image_slots.clear = zipPath
  exportedLoc.primary_image_slot = "clear"
  exportedLoc.image_url = zipPath
}
```

## 3.5. Не падать при недоступной картинке

Если `fetch(url)` не удался:

```text
- не падать всем export;
- записать warning;
- сохранить slot как null или legacy URL с пометкой.
```

Минимально:

```ts
try {
  ...
} catch (e) {
  console.warn(`Failed to export image ${loc.id}/${slot}`, e);
  exportedLoc.image_slots[slot] = null;
}
```

## 3.6. Импорт v2

При импорте ZIP:

1. прочитать `map.json`;
2. определить `schema_version`;
3. если `schema_version >= 2`, прочитать `image_slots`;
4. импортировать карту без финальных media URLs или с пустыми `image_slots`;
5. после успешного импорта пройти по ZIP images;
6. для каждого `location/slot`:
   - найти файл внутри ZIP;
   - превратить в `File`;
   - загрузить через `locationsApi.uploadImage(contextId, locId, file, slot)`;
   - получить новый URL;
   - записать его в imported location `image_slots[slot]`;
7. после upload выставить `primary_image_slot`.

Рекомендуемый порядок:

```text
1. debug_import_full_map без картинок или с пустыми image_slots.
2. uploadImage(contextId, locId, file, slot) для каждого slot.
3. debug_set_location_primary_image для primary slot.
4. refresh/resync projection.
```

Так безопаснее, потому что upload endpoint требует, чтобы location уже существовала в state.

## 3.7. Импорт старого v1 формата

Нужно поддержать старый экспорт, где была только одна картинка:

```json
{
  "locations": {
    "loc_A": {
      "image_url": "images/loc_A.jpg"
    }
  }
}
```

Migration при import:

```ts
if (!loc.image_slots && loc.image_url) {
  loc.image_slots = {
    clear: loc.image_url,
    fog: null,
    rain: null,
    night_clear: null,
    night_rain: null,
  };
  loc.primary_image_slot = "clear";
}
```

Если в ZIP есть старый файл:

```text
images/<loc_id>.<ext>
```

загрузить его в slot `clear`.

## 3.8. Backend `debug_import_full_map`

В `world_rules.py` команда `debug_import_full_map` должна:

- принимать `image_slots`;
- принимать `primary_image_slot`;
- мигрировать старый `image_url`;
- валидировать slots;
- синхронизировать `image_url`;
- bump `map_revision`;
- не терять `debug_layout`.

Pseudo:

```python
if command_type == "debug_import_full_map":
    imported_locations = payload.get("locations") or {}
    for loc in imported_locations.values():
        migrate_location_images(loc)
        _sync_location_primary_image_url(loc)
    state["locations"] = imported_locations
    state["debug_layout"] = payload.get("debug_layout") or state.get("debug_layout") or {}
    state["map_revision"] = int(state.get("map_revision", 0)) + 1
```

Если frontend импортирует карту сначала без картинок, backend должен принять пустые image_slots.

## 3.9. Acceptance criteria для import/export

```text
[ ] Экспорт карты с 5 заполненными слотами сохраняет все 5 картинок в ZIP.
[ ] map.json содержит schema_version = 2.
[ ] map.json содержит image_slots и primary_image_slot.
[ ] Импорт v2 восстанавливает все 5 картинок.
[ ] Импорт v2 сохраняет выбранный primary_image_slot.
[ ] После импорта image_url указывает на primary slot.
[ ] Старый v1 import с одним image_url работает и кладёт картинку в clear.
[ ] Экспорт не падает, если одна картинка недоступна.
[ ] Импорт не падает, если один slot отсутствует в ZIP.
[ ] После refresh страницы imported images остаются.
```

---

# 4. Move image upload/delete/download to `LocationModal`

## 4.1. Current problem

`LocationModal` сейчас поддерживает только single image:

```ts
imageFile?: File | null
initialImageUrl?: string | null
previewUrl
handleImageChange()
handleRemoveImage()
```

Нужно заменить на multi-slot model.

## 4.2. Update `LocationSaveData`

Старое:

```ts
export interface LocationSaveData {
  name: string;
  terrainType: string;
  anomalyActivity: number;
  dominantAnomalyType: string;
  region: string;
  exitZone: boolean;
  imageFile?: File | null;
}
```

Новое:

```ts
export interface LocationSaveData {
  name: string;
  terrainType: string;
  anomalyActivity: number;
  dominantAnomalyType: string;
  region: string;
  exitZone: boolean;
}
```

Image upload/delete/download should be handled by dedicated slot handlers, not as part of save payload.

## 4.3. New `LocationModal` props

Add:

```ts
import type { LocationImageSlot, LocationImageSlots } from './types';

export function LocationModal({
  ...
  initialImageSlots,
  initialPrimaryImageSlot,
  initialImageUrl,
  onUploadImageSlot,
  onDeleteImageSlot,
  onSetPrimaryImageSlot,
}: {
  ...
  initialImageSlots?: LocationImageSlots;
  initialPrimaryImageSlot?: LocationImageSlot | null;
  initialImageUrl?: string | null;
  onUploadImageSlot?: (slot: LocationImageSlot, file: File) => Promise<void>;
  onDeleteImageSlot?: (slot: LocationImageSlot) => Promise<void>;
  onSetPrimaryImageSlot?: (slot: LocationImageSlot) => Promise<void>;
})
```

## 4.4. Local modal image state

The modal should have local image state to update previews immediately after upload/delete without forcing full parent re-render.

```ts
const [imageSlots, setImageSlots] = useState<LocationImageSlots>(() => {
  const slots = { ...(initialImageSlots ?? {}) };
  if (!Object.values(slots).some(Boolean) && initialImageUrl) {
    slots.clear = initialImageUrl;
  }
  return slots;
});

const [primaryImageSlot, setPrimaryImageSlot] = useState<LocationImageSlot | null>(
  initialPrimaryImageSlot ?? (imageSlots.clear ? 'clear' : null),
);

const [slotBusy, setSlotBusy] = useState<Partial<Record<LocationImageSlot, boolean>>>({});
```

## 4.5. Slot card UI

Render all 5 cards:

```tsx
<div style={slotGridStyle}>
  {LOCATION_IMAGE_SLOTS.map(slot => (
    <LocationImageSlotCard
      key={slot}
      slot={slot}
      label={LOCATION_IMAGE_SLOT_LABELS[slot]}
      icon={LOCATION_IMAGE_SLOT_ICONS[slot]}
      url={imageSlots[slot]}
      isPrimary={primaryImageSlot === slot}
      busy={slotBusy[slot]}
      onUpload={(file) => handleUploadSlot(slot, file)}
      onDelete={() => handleDeleteSlot(slot)}
      onDownload={() => handleDownloadSlot(slot)}
      onSetPrimary={() => handleSetPrimarySlot(slot)}
    />
  ))}
</div>
```

Desktop layout:

```ts
gridTemplateColumns: 'repeat(5, minmax(120px, 1fr))'
```

For narrow screens:

```ts
gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))'
```

## 4.6. Upload button per slot

Each slot has its own hidden input:

```tsx
<label style={buttonStyle}>
  📤 Загрузить
  <input
    type="file"
    accept="image/jpeg,image/png,image/webp,image/gif"
    style={{ display: 'none' }}
    onChange={(e) => {
      const file = e.target.files?.[0];
      if (file) onUpload(file);
      e.currentTarget.value = '';
    }}
  />
</label>
```

No dropdown.

## 4.7. Download image button per slot

Add button:

```tsx
<button
  disabled={!url}
  onClick={() => downloadImage(url, `${locId}_${slot}`)}
>
  ⬇ Скачать картинку
</button>
```

Helper:

```ts
async function downloadImage(url: string, filenameBase: string) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to download image: ${res.status}`);
  const blob = await res.blob();
  const ext = extensionFromContentType(blob.type) ?? extensionFromUrl(url) ?? '.jpg';
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = objectUrl;
  a.download = `${filenameBase}${ext}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objectUrl);
}
```

## 4.8. Delete image per slot

Use backend response as source of truth:

```ts
const res = await onDeleteImageSlot?.(slot);
setImageSlots(res.image_slots);
setPrimaryImageSlot(res.primary_image_slot);
```

If handler does not return response yet, update optimistic state and refresh projection after operation.

## 4.9. Set primary per slot

```ts
async function handleSetPrimarySlot(slot: LocationImageSlot) {
  if (!imageSlots[slot]) return;
  setSlotBusy((prev) => ({ ...prev, [slot]: true }));
  try {
    await onSetPrimaryImageSlot?.(slot);
    setPrimaryImageSlot(slot);
  } finally {
    setSlotBusy((prev) => ({ ...prev, [slot]: false }));
  }
}
```

## 4.10. Save button should not upload images

The modal `Save` should only save text/location fields:

```text
name
terrain
anomaly
region
exit_zone
```

Image operations are immediate slot actions.

## 4.11. Create mode

For create mode, there is no `locId` yet, so slot uploads should be disabled until location exists.

In create mode:

```text
"Сначала создайте локацию, затем добавьте изображения в режиме редактирования."
```

---

# 5. Simplify `LocationDetailPanel`: primary selection only

## 5.1. Remove upload/delete controls from detail panel

Remove from `LocationDetailPanel`:

```ts
selectedSlot
imgUploading
fileInputRef
handleFileChange
onUploadLocationImageSlot
onDeleteLocationImageSlot
```

Keep only:

```ts
onSetPrimaryImageSlot?: (slot: LocationImageSlot) => Promise<void>;
```

## 5.2. Detail panel image section

Target:

```tsx
<Section label="🖼 Изображение локации">
  <PrimaryImagePreview url={primaryImageUrl} locName={loc.name} />
  <ImageSlotPrimaryButtons
    imageSlots={loc.image_slots}
    primaryImageSlot={loc.primary_image_slot}
    onSetPrimaryImageSlot={onSetPrimaryImageSlot}
  />
</Section>
```

Slot buttons:

```text
- enabled only if slot has image;
- active state for primary;
- no upload;
- no delete;
- no select dropdown.
```

## 5.3. Make image preview isolated

Move preview to memoized component:

```tsx
const LocationPrimaryImagePreview = React.memo(function LocationPrimaryImagePreview({
  url,
  name,
}: {
  url: string | null;
  name: string;
}) {
  if (!url) return <EmptyImagePlaceholder />;
  return <img key={url} src={url} alt={name} ... />;
});
```

And slot buttons:

```tsx
const LocationPrimaryImageSelector = React.memo(...)
```

## 5.4. Do not remount whole detail panel

Check `DebugMapPage.tsx` where `LocationDetailPanel` is rendered.

Avoid any key that changes on:

```text
loc.image_url
loc.primary_image_slot
zoneState.state_revision
zoneState.map_revision
```

Bad:

```tsx
<LocationDetailPanel key={`${selectedLocId}-${zoneState.state_revision}`} ... />
```

Good:

```tsx
<LocationDetailPanel key={selectedLocId ?? 'none'} ... />
```

or no key.

## 5.5. Optimistic local state for primary switch

Currently switching primary likely triggers:

```text
sendCommand(...)
→ global zoneState update
→ detail panel re-renders all content
```

To make only image change visually:

```ts
const [localPrimaryImageSlotByLoc, setLocalPrimaryImageSlotByLoc] = useState<Record<string, LocationImageSlot>>({});
```

When user clicks primary:

```ts
setLocalPrimaryImageSlotByLoc(prev => ({ ...prev, [loc.id]: slot }));
await sendCommand('debug_set_location_primary_image', { loc_id: loc.id, slot });
```

For rendering:

```ts
const effectivePrimarySlot =
  localPrimaryImageSlotByLoc[loc.id] ?? loc.primary_image_slot;
const effectivePrimaryUrl =
  loc.image_slots?.[effectivePrimarySlot] ?? getPrimaryLocationImageUrl(loc);
```

On zoneState update, if backend primary equals local override, clear override.

## 5.6. Preserve scroll

Switching primary must not do:

```ts
setSelectedLocId(null)
setSelectedLocId(loc.id)
```

Only send command/update local primary.

---

# 6. Update `DebugMapPage` handlers

## 6.1. New prop split

New target:

```text
LocationDetailPanel:
  onSetPrimaryImageSlot only

LocationModal:
  onUploadImageSlot
  onDeleteImageSlot
  onSetPrimaryImageSlot
```

## 6.2. Handler return values

`locationsApi.uploadImage(...)` and `deleteImage(...)` return updated payload.

Use that to patch local state if applicable.

If `zoneState` is global prop and cannot be patched locally, maintain local override maps:

```ts
const [localImageSlotsByLoc, setLocalImageSlotsByLoc] = useState<Record<string, LocationImageSlots>>({});
const [localPrimaryImageSlotByLoc, setLocalPrimaryImageSlotByLoc] = useState<Record<string, LocationImageSlot | null>>({});
```

When upload returns:

```ts
setLocalImageSlotsByLoc(prev => ({
  ...prev,
  [locId]: res.data.image_slots,
}));
setLocalPrimaryImageSlotByLoc(prev => ({
  ...prev,
  [locId]: res.data.primary_image_slot,
}));
```

When rendering modal/detail:

```ts
const locForUi = {
  ...loc,
  image_slots: localImageSlotsByLoc[loc.id] ?? loc.image_slots,
  primary_image_slot: localPrimaryImageSlotByLoc[loc.id] ?? loc.primary_image_slot,
};
```

Clear local overrides when server state catches up.

## 6.3. Passing props to `LocationModal`

```tsx
<LocationModal
  mode="edit"
  locId={editingLocId}
  initialName={loc.name}
  ...
  initialImageUrl={loc.image_url}
  initialImageSlots={effectiveImageSlots}
  initialPrimaryImageSlot={effectivePrimarySlot}
  onUploadImageSlot={(slot, file) => handleUploadLocationImageSlot(editingLocId, slot, file)}
  onDeleteImageSlot={(slot) => handleDeleteLocationImageSlot(editingLocId, slot)}
  onSetPrimaryImageSlot={(slot) => handleSetPrimaryImageSlot(editingLocId, slot)}
/>
```

## 6.4. Save location fields

`onSave` should no longer handle `imageFile`.

New flow:

```ts
sendCommand('debug_update_location', {
  loc_id,
  name,
  terrain_type,
  anomaly_activity,
  dominant_anomaly_type,
  region,
  exit_zone,
})
```

Images are managed separately.

---

# 7. Backend support for download

No special backend endpoint is needed if images are served under `/media/...`.

Frontend can download current image URL directly with `fetch(url)`.

If auth/cookies are needed:

```ts
fetch(url, { credentials: 'include' })
```

---

# 8. Tests

## 8.1. Import/export tests

Recommended extraction:

```text
frontend/src/games/zone_stalkers/ui/debugMap/mapArchive.ts
```

Functions:

```ts
exportMapToZip(...)
importMapFromZip(...)
normalizeExportedLocationImages(...)
migrateImportedLocationImages(...)
```

Tests:

```text
mapArchive.test.ts
```

Cases:

```text
[ ] v2 export includes all non-empty image_slots.
[ ] v2 export maps primary_image_slot to image_url path.
[ ] v2 import maps all slot files to upload calls.
[ ] v1 import maps image_url to clear slot.
[ ] missing slot image does not throw.
[ ] failed image fetch records warning but export continues.
```

## 8.2. Backend tests

Backend `debug_import_full_map` tests:

```python
def test_debug_import_full_map_accepts_image_slots():
    ...

def test_debug_import_full_map_migrates_legacy_image_url_to_clear():
    ...

def test_debug_import_full_map_rejects_invalid_primary_slot():
    ...

def test_debug_import_full_map_syncs_image_url_to_primary_slot():
    ...
```

## 8.3. Manual QA

```text
[ ] Existing single-image map exports and imports.
[ ] New 5-slot map exports and imports.
[ ] After import all 5 images are visible in edit modal.
[ ] Primary slot after import is preserved.
[ ] Detail panel can switch primary slot only.
[ ] Detail panel has no upload/delete controls.
[ ] Edit modal has 5 visible slot cards, no dropdown.
[ ] Each slot upload works independently.
[ ] Each slot download works.
[ ] Each slot delete works.
[ ] Switching primary does not reset detail panel scroll or reload whole panel.
```

---

# 9. Suggested implementation order

1. Extract map import/export helpers from `DebugMapPage.tsx` into `debugMap/mapArchive.ts`.
2. Add schema v2 to export format.
3. Export all `image_slots` and ZIP image files.
4. Support v1 import migration from single `image_url`.
5. Update `debug_import_full_map` backend to accept/migrate/sync slots.
6. Update `LocationModal` props and remove single-image `imageFile` logic.
7. Add 5-slot image editor cards to `LocationModal`.
8. Move upload/delete/download controls to `LocationModal`.
9. Remove upload/delete/dropdown from `LocationDetailPanel`.
10. Keep only primary slot selector in detail panel.
11. Add memoized primary image preview and optimistic primary switch override.
12. Add tests/manual QA.

---

# 10. Acceptance criteria

## Import/export

```text
[ ] Export v2 creates ZIP with map.json and all slot image files.
[ ] Import v2 restores all slot images.
[ ] Import v2 restores primary_image_slot.
[ ] Import v2 sets image_url to primary slot URL.
[ ] Import v1 with one image_url still works.
[ ] Export/import does not drop debug_layout, positions, regions, connections.
[ ] Export/import does not break maps with no images.
```

## Location edit modal

```text
[ ] Edit modal shows 5 image slots at once.
[ ] No dropdown is used for image slot selection.
[ ] Each slot has its own upload button.
[ ] Each slot has its own download button if image exists.
[ ] Each slot has delete button if image exists.
[ ] Each slot can be made primary if image exists.
[ ] Create mode does not allow upload until location exists.
```

## Debug detail panel

```text
[ ] Detail panel shows primary image.
[ ] Detail panel can only switch primary image.
[ ] Detail panel has no upload/delete/download image controls.
[ ] Switching primary changes only image/slot active state visually.
[ ] Switching primary does not reset selected location, scroll, or whole menu.
```

---

# 11. Files likely touched

```text
frontend/src/games/zone_stalkers/ui/DebugMapPage.tsx
frontend/src/games/zone_stalkers/ui/debugMap/DetailPanels.tsx
frontend/src/games/zone_stalkers/ui/debugMap/Modals.tsx
frontend/src/games/zone_stalkers/ui/debugMap/types.ts
frontend/src/games/zone_stalkers/ui/debugMap/styles.ts
frontend/src/games/zone_stalkers/ui/debugMap/mapArchive.ts
frontend/src/api/client.ts

backend/app/games/zone_stalkers/rules/world_rules.py
backend/app/games/zone_stalkers/location_images.py
backend/tests/test_zone_stalkers_location_images.py
backend/tests/test_zone_stalkers_debug_import_map.py
```

Optional frontend tests:

```text
frontend/src/games/zone_stalkers/ui/debugMap/mapArchive.test.ts
```

---

# 12. Important notes

## 12.1. Keep `image_url`

Do not remove `image_url`.

It remains compatibility/derived field:

```text
image_url = active primary slot URL
```

## 12.2. Import should not preserve old `/media` URLs as final URLs

During import, ZIP image files must be uploaded and converted to fresh `/media/...` URLs in the current context.

Bad:

```json
"image_slots": {
  "clear": "/media/locations/old-context/loc_A/clear/old.jpg"
}
```

Good:

```json
"image_slots": {
  "clear": "/media/locations/new-context/loc_A/clear/new-uuid.jpg"
}
```

## 12.3. Detail panel should be read-mostly

The location detail panel should remain lightweight:

```text
view details
choose primary image
spawn entities
inspect occupants
```

Heavy image management belongs in the edit modal.

## 12.4. Optimize visual update before performance micro-optimization

The immediate issue is UX: switching primary appears to reload the whole menu. Fix it structurally by:

```text
- not remounting panel;
- memoizing preview;
- optimistic local primary state;
- avoiding selectedLocId reset.
```

Only after that consider React Profiler-level optimizations.
