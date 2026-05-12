# Copilot task: исправить баг повторной загрузки изображений локаций

## Контекст

В `Zone Stalkers` есть debug-функция загрузки изображения для локации.

Проблема: при последующих загрузках изображения на одну и ту же локацию UI иногда начинает показывать одно из старых изображений.

Симптом очень похож на cache/stale URL bug:

```text
1. Пользователь загружает image A на loc_X.
2. Локация показывает image A.
3. Пользователь загружает image B на loc_X.
4. UI иногда продолжает показывать image A или одну из старых картинок.
```

## Где искать

Основные файлы:

```text
backend/app/games/zone_stalkers/router.py
backend/app/games/zone_stalkers/models.py
backend/app/games/zone_stalkers/rules/world_rules.py
frontend/src/api/client.ts
frontend/src/games/zone_stalkers/ui/DebugMapPage.tsx
frontend/src/games/zone_stalkers/ui/debugMap/DetailPanels.tsx
```

---

# 1. Диагноз

## 1.1. Backend сохраняет файл по стабильному URL

Сейчас upload endpoint сохраняет изображение примерно так:

```python
rel_dir = os.path.join("locations", str(context_id))
rel_path = os.path.join(rel_dir, f"{location_id}{ext}")
abs_path = os.path.join(MEDIA_ROOT, rel_path)
...
url = f"/media/{rel_path}"
```

То есть для одного `context_id + location_id + ext` URL всегда одинаковый:

```text
/media/locations/<context_id>/<location_id>.jpg
```

Если повторно загрузить другой `.jpg`, файл физически перезапишется по тому же пути, но URL не изменится.

Браузер, React `<img>`, dev-server/proxy или StaticFiles могут продолжать отдавать старое изображение из кеша, потому что `src` остался тем же.

## 1.2. Frontend рендерит картинку напрямую по `loc.image_url`

В `DetailPanels.tsx` используется:

```tsx
<img src={loc.image_url} ... />
```

Если после новой загрузки `loc.image_url` строково не поменялся, React не обязан пересоздавать image resource, а браузер может использовать кеш.

## 1.3. Модель `LocationImage` не гарантирует одну запись на location

`LocationImage` хранит:

```python
context_id
location_id
filename
content_type
file_path
created_at
```

Но в модели нет уникального ограничения на `(context_id, location_id)`.

Если из-за гонки, ошибки или старых данных в таблице появятся несколько записей для одной локации, текущий upload/delete код с `.first()` может удалить не ту запись и оставить старые файлы/metadata.

---

# 2. Что нужно исправить

Нужно сделать загрузку изображений **cache-safe** и **state-consistent**.

Главное правило:

```text
Каждая новая загрузка изображения должна получать новый уникальный public URL.
```

Не нужно перезаписывать файл по тому же `/media/.../<location_id>.jpg`.

Лучший вариант:

```text
/media/locations/<context_id>/<location_id>/<image_id>.<ext>
```

или:

```text
/media/locations/<context_id>/<location_id>_<uuid>.<ext>
```

Тогда при каждой загрузке `image_url` меняется, и браузер гарантированно загружает новую картинку.

---

# 3. Backend changes

## 3.1. Изменить путь сохранения файла

В `backend/app/games/zone_stalkers/router.py`, endpoint:

```python
@router.post("/locations/{context_id}/{location_id}/image")
async def upload_location_image(...)
```

заменить stable filename:

```python
rel_path = os.path.join(rel_dir, f"{location_id}{ext}")
```

на unique filename.

Рекомендуемый вариант:

```python
image_id = uuid.uuid4().hex
rel_dir = os.path.join("locations", str(context_id), location_id)
rel_path = os.path.join(rel_dir, f"{image_id}{ext}")
abs_dir = os.path.join(MEDIA_ROOT, rel_dir)
abs_path = os.path.join(MEDIA_ROOT, rel_path)
```

Public URL:

```python
url = f"/media/{rel_path}"
```

Важно: использовать `/` в URL. `os.path.join` на Linux даст `/`, но лучше явно нормализовать:

```python
url = "/media/" + rel_path.replace(os.sep, "/")
```

## 3.2. Удалять все старые записи и файлы для локации

Сейчас код ищет одну запись:

```python
existing = db.query(LocationImage).filter(...).first()
```

Нужно удалить все:

```python
existing_records = (
    db.query(LocationImage)
    .filter(
        LocationImage.context_id == context_id,
        LocationImage.location_id == location_id,
    )
    .all()
)

for existing in existing_records:
    old_abs = os.path.join(MEDIA_ROOT, existing.file_path)
    try:
        os.remove(old_abs)
    except FileNotFoundError:
        pass
    db.delete(existing)
```

Это закроет старые дубликаты.

## 3.3. Добавить уникальность на `(context_id, location_id)`

В `backend/app/games/zone_stalkers/models.py` добавить `UniqueConstraint`.

Пример:

```python
from sqlalchemy import Column, String, DateTime, ForeignKey, UniqueConstraint

class LocationImage(Base):
    __tablename__ = "location_images"

    __table_args__ = (
        UniqueConstraint("context_id", "location_id", name="uq_location_images_context_location"),
    )
```

Также нужна миграция Alembic, если проект использует миграции.

Если Alembic нет или миграции пока не используются, оставить TODO в комментарии и добавить cleanup logic через `.all()` как обязательную защиту.

## 3.4. Атомарно обновлять `state.locations[location_id].image_url`

Сейчас upload endpoint только сохраняет файл и возвращает URL, а потом frontend должен отдельно отправить `debug_update_location` с `image_url`.

Это создаёт риск рассинхрона:

```text
file uploaded
но state image_url не обновился
или обновился старым URL
или command не дошёл
```

Лучше сделать upload endpoint источником истины для image_url.

После сохранения файла и DB record:

```python
from app.core.state_cache.service import load_context_state, save_context_state

state = load_context_state(ctx.id, ctx)
loc = state.get("locations", {}).get(location_id)
if loc is None:
    raise HTTPException(status_code=404, detail="Location not found in zone state")

loc["image_url"] = url
state["state_revision"] = int(state.get("state_revision", 0)) + 1
state["map_revision"] = int(state.get("map_revision", 0)) + 1

save_context_state(ctx.id, state, ctx, force_persist=True)
```

Return:

```python
return {
    "url": url,
    "image_url": url,
    "location_id": location_id,
    "state_revision": state.get("state_revision"),
    "map_revision": state.get("map_revision"),
}
```

### Важный порядок

Сначала проверить, что location существует в state.

Лучше в начале endpoint:

```python
state = load_context_state(ctx.id, ctx)
if location_id not in state.get("locations", {}):
    raise HTTPException(status_code=404, detail="Location not found in zone state")
```

Только потом читать/писать файл.

## 3.5. Исправить delete endpoint

В `DELETE /locations/{context_id}/{location_id}/image`:

1. Удалить все `LocationImage` records для location.
2. Удалить все файлы этих records.
3. Если каталог `MEDIA_ROOT/locations/<context_id>/<location_id>` пустой — удалить каталог.
4. Очистить `state.locations[location_id].image_url`.
5. Увеличить `state_revision` и `map_revision`.
6. Сохранить state.

Сейчас endpoint возвращает `204`. Можно оставить `204`, но удобнее вернуть JSON:

```python
return {
    "status": "deleted",
    "location_id": location_id,
    "state_revision": state.get("state_revision"),
    "map_revision": state.get("map_revision"),
}
```

Если хочется сохранить `204`, тогда frontend должен сам делать локальное обновление или refresh. Но для debug UX лучше JSON.

## 3.6. Не использовать query-param как основной фикс

Можно добавить `?v=<uuid>` к URL, но лучше не полагаться только на query string.

Хорошо:

```text
/media/locations/<context_id>/<location_id>/<uuid>.jpg
```

Допустимо дополнительно:

```text
/media/locations/<context_id>/<location_id>/<uuid>.jpg?v=<state_revision>
```

Но главный фикс — уникальный filename.

---

# 4. Frontend changes

## 4.1. После upload не отправлять старый URL

В `DebugMapPage.tsx` найти место, где используется:

```ts
locationsApi.uploadImage(...)
```

Сейчас вероятно после upload вызывается:

```ts
sendCommand("debug_update_location", { ..., image_url: response.data.url })
```

После backend fix это можно оставить как fallback, но лучше изменить на:

```text
upload endpoint already updates state
```

Рекомендуемый вариант:

```ts
const res = await locationsApi.uploadImage(contextId, locId, file);
const imageUrl = res.data.image_url ?? res.data.url;
```

Дальше нужно обновить локальное состояние или дождаться `zone_delta`.

Если `zone_delta` от upload endpoint не отправляется, то после upload можно вызвать scoped projection/dynamic refresh или локально обновить selected location image.

Минимально:

```ts
await sendCommand("debug_update_location", {
  loc_id: locId,
  name,
  terrain_type,
  anomaly_activity,
  dominant_anomaly_type,
  region,
  exit_zone,
  image_url: imageUrl,
});
```

Но если backend endpoint уже обновил state, повторный command не обязателен.

## 4.2. Добавить `key={loc.image_url}` для `<img>`

В `DetailPanels.tsx` заменить:

```tsx
<img src={loc.image_url} ... />
```

на:

```tsx
<img
  key={loc.image_url}
  src={loc.image_url}
  alt={loc.name}
  style={{ width: '100%', borderRadius: 6, objectFit: 'cover', maxHeight: 200, border: '1px solid #1e3a5f' }}
/>
```

Это заставит React пересоздать `<img>`, когда URL изменится.

## 4.3. Не использовать `Date.now()` в src

Не делать так:

```tsx
src={`${loc.image_url}?v=${Date.now()}`}
```

Это будет перезагружать картинку на каждый render.

Если нужен cache-bust для legacy stable URLs, использовать стабильную версию:

```tsx
src={`${loc.image_url}?v=${zoneState.map_revision ?? zoneState.state_revision}`}
```

Но после backend unique URL это не должно быть нужно.

## 4.4. Обновить типы ответа uploadImage

В `frontend/src/api/client.ts` сейчас `uploadImage` возвращает `{ url: string }`.

Расширить тип:

```ts
uploadImage: (contextId: string, locationId: string, file: File) => {
  ...
  return apiClient.post<{
    url: string;
    image_url?: string;
    location_id?: string;
    state_revision?: number;
    map_revision?: number;
  }>(...);
}
```

---

# 5. API compatibility

`locationsApi.uploadImage` сейчас ожидает:

```ts
{ url: string }
```

После backend change вернуть superset:

```json
{
  "url": "/media/locations/.../uuid.jpg",
  "image_url": "/media/locations/.../uuid.jpg",
  "location_id": "loc_X",
  "state_revision": 123,
  "map_revision": 5
}
```

Это не сломает старый frontend, потому что `url` остаётся.

Для `deleteImage`, если поменять 204 на JSON, обновить frontend client type. Если хочется избежать breaking change, оставить 204 и просто очистить state на backend.

---

# 6. Tests

## 6.1. Backend tests

Добавить тесты:

```text
backend/tests/test_zone_stalkers_location_images.py
```

### test_upload_same_location_returns_unique_url

```python
def test_upload_same_location_returns_unique_url(client, auth_headers, zone_context):
    res1 = upload_image("a.jpg", b"image-a")
    res2 = upload_image("b.jpg", b"image-b")

    assert res1.json()["url"] != res2.json()["url"]
```

### test_second_upload_deletes_old_file_and_old_db_rows

```python
def test_second_upload_deletes_old_file_and_old_db_rows(...):
    first = upload_image(...)
    first_path = media_path_from_url(first["url"])

    second = upload_image(...)
    assert not first_path.exists()

    rows = db.query(LocationImage).filter_by(context_id=ctx.id, location_id=loc_id).all()
    assert len(rows) == 1
    assert rows[0].file_path in second["url"]
```

### test_upload_updates_location_state_image_url

```python
def test_upload_updates_location_state_image_url(...):
    res = upload_image(...)
    state = load_context_state(ctx.id, ctx)
    assert state["locations"][loc_id]["image_url"] == res.json()["url"]
```

### test_delete_image_clears_state_and_files

```python
def test_delete_image_clears_state_and_files(...):
    uploaded = upload_image(...)
    delete_image(...)
    state = load_context_state(ctx.id, ctx)
    assert state["locations"][loc_id].get("image_url") is None
```

### test_upload_rejects_missing_location

```python
def test_upload_rejects_missing_location(...):
    res = upload_image(location_id="no_such_loc")
    assert res.status_code == 404
```

## 6.2. Frontend / manual checks

Manual checklist:

```text
[ ] Загрузить картинку A на локацию.
[ ] Убедиться, что она отображается.
[ ] Загрузить картинку B того же расширения на ту же локацию.
[ ] Убедиться, что сразу отображается B, без hard refresh.
[ ] Загрузить картинку C другого расширения.
[ ] Убедиться, что отображается C.
[ ] Удалить изображение.
[ ] Убедиться, что оно исчезло из detail panel.
[ ] Перезагрузить страницу.
[ ] Убедиться, что старое изображение не вернулось.
```

---

# 7. Important implementation notes

## 7.1. Не оставлять старый stable URL

Не делать так:

```python
rel_path = f"locations/{context_id}/{location_id}.jpg"
```

Даже если добавить cache headers, это всё равно хрупко.

## 7.2. Не полагаться только на frontend cache-bust

Frontend cache-busting полезен, но основной фикс должен быть backend-side unique URL.

## 7.3. Удалять старые файлы безопасно

При удалении старых файлов:

```python
try:
    os.remove(old_abs)
except FileNotFoundError:
    pass
```

Не падать, если файл уже удалён.

## 7.4. Проверить старые records

Если в DB уже есть дубликаты `LocationImage`, новый upload должен очистить их все.

## 7.5. Синхронизация с state

После upload/delete state должен быть консистентным:

```text
LocationImage row
file on disk
state.locations[loc_id].image_url
```

Все три должны указывать на один актуальный файл или быть очищены.

---

# 8. Suggested patch sketch

## 8.1. Backend upload pseudo-code

```python
@router.post("/locations/{context_id}/{location_id}/image")
async def upload_location_image(...):
    ctx = ...
    if not ctx:
        raise HTTPException(404, "Context not found")

    state = load_context_state(ctx.id, ctx)
    loc = state.get("locations", {}).get(location_id)
    if loc is None:
        raise HTTPException(404, "Location not found in zone state")

    validate content_type
    contents = await file.read(MAX_IMAGE_SIZE + 1)
    validate size

    ext = _EXT_MAP[content_type]
    image_id = uuid.uuid4().hex

    rel_dir = os.path.join("locations", str(context_id), location_id)
    rel_path = os.path.join(rel_dir, f"{image_id}{ext}")
    abs_dir = os.path.join(MEDIA_ROOT, rel_dir)
    abs_path = os.path.join(MEDIA_ROOT, rel_path)

    old_records = db.query(LocationImage).filter(...).all()
    for old in old_records:
        delete old.file_path
        db.delete(old)

    os.makedirs(abs_dir, exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(contents)

    record = LocationImage(
        context_id=context_id,
        location_id=location_id,
        filename=file.filename or f"{image_id}{ext}",
        content_type=content_type,
        file_path=rel_path,
    )
    db.add(record)

    url = "/media/" + rel_path.replace(os.sep, "/")
    loc["image_url"] = url
    state["state_revision"] = int(state.get("state_revision", 0)) + 1
    state["map_revision"] = int(state.get("map_revision", 0)) + 1
    save_context_state(ctx.id, state, ctx, force_persist=True)

    db.commit()

    return {
        "url": url,
        "image_url": url,
        "location_id": location_id,
        "state_revision": state["state_revision"],
        "map_revision": state["map_revision"],
    }
```

## 8.2. Frontend image render

```tsx
{loc.image_url && (
  <Section label="🖼 Изображение">
    <img
      key={loc.image_url}
      src={loc.image_url}
      alt={loc.name}
      style={{ width: '100%', borderRadius: 6, objectFit: 'cover', maxHeight: 200, border: '1px solid #1e3a5f' }}
    />
  </Section>
)}
```

---

# 9. Acceptance criteria

```text
[ ] Every upload to the same location returns a new unique URL.
[ ] Re-upload with the same extension shows the new image immediately.
[ ] Re-upload with a different extension shows the new image immediately.
[ ] Old image files are deleted.
[ ] Duplicate LocationImage rows are cleaned up.
[ ] LocationImage has unique constraint or code guarantees single active record.
[ ] state.locations[loc_id].image_url is updated by upload endpoint.
[ ] delete endpoint clears image_url.
[ ] Frontend img uses key={loc.image_url}.
[ ] Existing API usage remains compatible via response.url.
[ ] Tests cover repeated upload and delete.
```

---

# 10. Why this fixes the bug

The current bug happens because the image resource identity is the URL.

If the URL stays the same:

```text
/media/locations/context/loc_A.jpg
```

the browser is allowed to reuse the old cached resource.

After the fix, every upload has a different URL:

```text
/media/locations/context/loc_A/8f3d....jpg
/media/locations/context/loc_A/a91c....jpg
```

So the browser sees a new resource and must request/display the new image.

Additionally, backend state and DB metadata are kept consistent, so old images cannot reappear after refresh or from stale `LocationImage` records.
