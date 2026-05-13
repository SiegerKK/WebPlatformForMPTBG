# Zone Stalkers — PR plan: bulk image optimization for location image slots

## Цель PR

Добавить debug/admin-фичу для массовой оптимизации изображений локаций.

Основной сценарий:

```text
500+ локаций
до 10 изображений на локацию
PNG-файлы по 3–4 MB
нужно резко уменьшить размер медиа без заметной визуальной деградации
```

По тестовой конвертации на 10 предоставленных PNG:

```text
PNG total:      36.33 MB
WebP q85:       3.35 MB
saving:         ~90.8%
avg per image:  ~343 KB
```

Поэтому базовый режим фичи:

```text
target_format = webp
quality = 85
method = 6
```

Это самый сильный вариант из протестированных, и визуально он подошёл.

---

# 1. Важное уточнение по качеству

Нельзя называть это технически “без потери качества”.

```text
PNG → WebP quality 85 = lossy recompression
```

Но для текущего типа изображений — атмосферные стилизованные арты локаций — визуальная разница почти незаметна, а экономия огромная.

В UI формулировать так:

```text
Оптимизировать изображения
```

а не:

```text
Сжать без потери качества
```

Подсказка:

```text
Изображения PNG будут перекодированы в WebP q85. Это lossy-сжатие, но для локационных артов обычно визуально почти незаметно. Перед заменой можно выполнить dry run.
```

---

# 2. Текущая модель хранения

Сейчас изображение локации хранится в двух местах:

## 2.1. DB metadata

Таблица:

```text
location_images
```

Модель:

```python
LocationImage:
  context_id
  location_id
  slot
  filename
  content_type
  file_path
```

Каждая запись соответствует одному image slot.

## 2.2. State blob

В state локации:

```json
{
  "image_slots": {
    "clear": "/media/locations/<ctx>/<loc>/clear/<uuid>.png",
    "fog": "/media/locations/<ctx>/<loc>/fog/<uuid>.png",
    "rain": "/media/locations/<ctx>/<loc>/rain/<uuid>.png",
    "night_clear": "/media/locations/<ctx>/<loc>/night_clear/<uuid>.png",
    "night_rain": "/media/locations/<ctx>/<loc>/night_rain/<uuid>.png"
  },
  "primary_image_slot": "clear",
  "image_url": "/media/locations/<ctx>/<loc>/clear/<uuid>.png"
}
```

После конвертации нужно обновлять оба слоя:

```text
LocationImage.file_path
LocationImage.content_type
LocationImage.filename
state.locations[loc_id].image_slots[slot]
state.locations[loc_id].image_url, если slot primary
state_revision
map_revision
```

---

# 3. Целевое поведение UI

## 3.1. Где добавить кнопку

В debug map добавить блок:

```text
Media tools / Изображения
```

Кнопки:

```text
[Оценить экономию]
[Оптимизировать PNG → WebP]
```

Лучший UX:

```text
1. Сначала dry run.
2. Показать сколько файлов будет обработано.
3. Показать потенциальную экономию.
4. Потом кнопка “Применить оптимизацию”.
```

## 3.2. UI flow

### Dry run

Пользователь нажимает:

```text
Оценить экономию
```

Frontend отправляет:

```json
{
  "target_format": "webp",
  "quality": 85,
  "dry_run": true,
  "replace_only_if_smaller": true,
  "min_saving_ratio": 0.15,
  "skip_transparent_png": false
}
```

Backend возвращает отчёт:

```json
{
  "dry_run": true,
  "processed": 5000,
  "convertible": 4760,
  "skipped": 240,
  "bytes_before": 19000000000,
  "estimated_bytes_after": 1700000000,
  "estimated_bytes_saved": 17300000000,
  "estimated_saving_ratio": 0.91
}
```

### Apply

Пользователь нажимает:

```text
Применить оптимизацию
```

Frontend отправляет тот же payload, но:

```json
{
  "dry_run": false
}
```

После завершения UI показывает:

```text
Оптимизировано: 4760
Пропущено: 240
Было: 18.1 GB
Стало: 1.7 GB
Сэкономлено: 16.4 GB
```

---

# 4. Backend endpoint

## 4.1. MVP endpoint

Добавить endpoint:

```text
POST /zone-stalkers/contexts/{context_id}/images/optimize
```

Payload:

```python
class OptimizeLocationImagesRequest(BaseModel):
    target_format: Literal["webp", "jpeg"] = "webp"
    quality: int = Field(default=85, ge=1, le=100)
    dry_run: bool = True
    replace_only_if_smaller: bool = True
    min_saving_ratio: float = Field(default=0.15, ge=0.0, le=1.0)
    source_content_types: list[str] = Field(default_factory=lambda: ["image/png"])
    skip_transparent_png: bool = False
    limit: int | None = Field(default=None, ge=1, le=10000)
```

Default:

```json
{
  "target_format": "webp",
  "quality": 85,
  "dry_run": true,
  "replace_only_if_smaller": true,
  "min_saving_ratio": 0.15,
  "source_content_types": ["image/png"],
  "skip_transparent_png": false
}
```

## 4.2. Почему `skip_transparent_png=false`

Для текущих локационных картинок прозрачность не нужна.

Если PNG содержит alpha, можно аккуратно положить его на тёмный фон:

```text
background = #020617
```

Это соответствует текущему тёмному UI и атмосфере.

Но в response обязательно считать такие случаи отдельно:

```json
{
  "transparent_flattened": 12
}
```

Если позже появятся UI-иконки/карты с прозрачностью, можно включить `skip_transparent_png=true`.

---

# 5. Конвертация

## 5.1. Зависимость

Добавить Pillow, если его нет:

```text
Pillow
```

В backend requirements.

## 5.2. Core helper

Создать файл:

```text
backend/app/games/zone_stalkers/media_optimizer.py
```

Пример:

```python
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from PIL import Image, ImageOps


@dataclass
class EncodedImage:
    data: bytes
    content_type: str
    extension: str
    transparent_flattened: bool


def encode_location_image(
    source_path: str,
    *,
    target_format: str = "webp",
    quality: int = 85,
    flatten_background: tuple[int, int, int] = (2, 6, 23),
    skip_transparent: bool = False,
) -> EncodedImage | None:
    with Image.open(source_path) as img:
        img = ImageOps.exif_transpose(img)

        has_alpha = (
            img.mode in ("RGBA", "LA")
            or (img.mode == "P" and "transparency" in img.info)
        )

        transparent_flattened = False

        if has_alpha:
            if skip_transparent:
                return None
            if img.mode == "P":
                img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, flatten_background)
            bg.paste(img, mask=img.getchannel("A"))
            img = bg
            transparent_flattened = True
        else:
            img = img.convert("RGB")

        out = io.BytesIO()

        if target_format == "webp":
            img.save(
                out,
                format="WEBP",
                quality=quality,
                method=6,
            )
            return EncodedImage(
                data=out.getvalue(),
                content_type="image/webp",
                extension=".webp",
                transparent_flattened=transparent_flattened,
            )

        if target_format == "jpeg":
            img.save(
                out,
                format="JPEG",
                quality=quality,
                optimize=True,
                progressive=True,
                subsampling=1,
            )
            return EncodedImage(
                data=out.getvalue(),
                content_type="image/jpeg",
                extension=".jpg",
                transparent_flattened=transparent_flattened,
            )

        raise ValueError(f"Unsupported target_format: {target_format}")
```

---

# 6. Safe replace algorithm

Критично: нельзя удалять старый PNG до успешного сохранения state и commit.

Для каждого `LocationImage`:

```text
1. Найти old_abs_path.
2. Прочитать и перекодировать.
3. Посчитать new_size.
4. Если dry_run — ничего не писать, только report.
5. Если replace_only_if_smaller:
     пропустить, если new_size >= old_size.
6. Если saving_ratio < min_saving_ratio:
     пропустить.
7. Записать новый файл рядом:
     locations/<context>/<location>/<slot>/<uuid>.webp
8. Обновить DB record:
     content_type = image/webp
     file_path = new_rel_path
     filename = <old_stem>.webp или <uuid>.webp
9. Обновить state:
     loc.image_slots[slot] = new_url
     sync_location_primary_image_url(loc)
10. save_context_state(...)
11. db.commit()
12. Только после commit удалить old_abs_path.
```

Если ошибка после записи нового файла:

```text
- rollback DB;
- invalidate_context_state;
- удалить новый файл;
- старый файл не трогать.
```

---

# 7. Bulk transaction strategy

## 7.1. Не делать один огромный commit на 5000 файлов

5000 файлов лучше обрабатывать батчами.

Recommended:

```text
batch_size = 50
```

Каждый batch:

```text
- конвертирует до 50 изображений;
- обновляет state;
- save_context_state;
- db.commit;
- удаляет old files после commit.
```

Плюсы:

```text
меньше риск долгой транзакции
меньше потерь при ошибке
можно показать progress
```

## 7.2. MVP можно сделать синхронным с limit

Для первого PR без job queue:

```text
limit=100
```

UI может запускать оптимизацию несколько раз.

Но для полного 5000 файлов лучше background job.

---

# 8. Background job вариант

Если в проекте уже есть task/job механизм — использовать его.

Если нет, MVP можно сделать так:

```text
POST /images/optimize-jobs
GET /images/optimize-jobs/{job_id}
```

Runtime in-memory job registry:

```python
IMAGE_OPTIMIZATION_JOBS: dict[str, dict] = {}
```

Job state:

```json
{
  "job_id": "...",
  "status": "running",
  "processed": 120,
  "converted": 110,
  "skipped": 10,
  "errors": 0,
  "bytes_before": 500000000,
  "bytes_after": 45000000,
  "bytes_saved": 455000000,
  "started_at": "...",
  "finished_at": null
}
```

Для production лучше Redis-backed job registry, но для debug/admin MVP достаточно memory registry.

---

# 9. Response schema

## 9.1. Per item

```json
{
  "location_id": "loc_A",
  "slot": "clear",
  "old_content_type": "image/png",
  "new_content_type": "image/webp",
  "old_size": 3819282,
  "new_size": 342081,
  "saving_ratio": 0.91,
  "converted": true,
  "skipped_reason": null,
  "old_url": "/media/locations/<ctx>/loc_A/clear/old.png",
  "new_url": "/media/locations/<ctx>/loc_A/clear/new.webp"
}
```

## 9.2. Summary

```json
{
  "context_id": "...",
  "dry_run": false,
  "target_format": "webp",
  "quality": 85,
  "processed": 5000,
  "converted": 4720,
  "skipped": 280,
  "errors": 0,
  "bytes_before": 18165000000,
  "bytes_after": 1675000000,
  "bytes_saved": 16490000000,
  "saving_ratio": 0.908,
  "transparent_flattened": 0,
  "state_revision": 123,
  "map_revision": 45,
  "items": []
}
```

For large runs, do not return all 5000 items by default.

Add:

```python
include_items: bool = False
```

or cap:

```text
items_preview_limit = 100
```

---

# 10. Frontend changes

## 10.1. API client

In:

```text
frontend/src/api/client.ts
```

Add:

```ts
export type OptimizeLocationImagesRequest = {
  target_format?: 'webp' | 'jpeg';
  quality?: number;
  dry_run?: boolean;
  replace_only_if_smaller?: boolean;
  min_saving_ratio?: number;
  source_content_types?: string[];
  skip_transparent_png?: boolean;
  limit?: number;
};

export const locationsApi = {
  ...
  optimizeImages(contextId: string, payload: OptimizeLocationImagesRequest) {
    return api.post(`/zone-stalkers/contexts/${contextId}/images/optimize`, payload);
  },
};
```

## 10.2. Debug UI block

In debug map page add panel:

```text
🖼 Оптимизация изображений
```

Controls:

```text
Target: WebP
Quality: 85
[Оценить экономию]
[Оптимизировать PNG → WebP]
```

For MVP, hardcode:

```text
WebP q85
```

and add only two buttons.

## 10.3. Confirmation text

Before apply:

```text
Будут перекодированы PNG-изображения всех локаций в WebP q85.
Это lossy-сжатие, но на тестовых локациях визуальная разница незаметна.
Старые PNG будут удалены только после успешного сохранения новых файлов.
Продолжить?
```

## 10.4. Result display

Show:

```text
Обработано: 5000
Сконвертировано: 4720
Пропущено: 280
Было: 18.1 GB
Стало: 1.7 GB
Сэкономлено: 16.4 GB
```

Use helper:

```ts
formatBytes(bytes)
formatPercent(ratio)
```

---

# 11. State update after optimization

After successful optimization frontend should refresh static map/projection, because image URLs changed.

Options:

```text
1. invalidate/refetch map-static;
2. full debug-map projection refresh;
3. update local loc.image_slots from response if include_items=true.
```

Recommended MVP:

```text
after optimize → refetch map-static / debug-map state
```

because many locations can change.

---

# 12. Tests

## 12.1. Backend unit tests

Add tests:

```text
backend/tests/test_zone_stalkers_image_optimizer.py
```

Cases:

```text
[ ] dry_run does not modify DB, state, or files.
[ ] PNG image converts to WebP q85.
[ ] LocationImage.content_type changes to image/webp.
[ ] LocationImage.file_path changes to .webp.
[ ] state.locations[loc_id].image_slots[slot] changes to new URL.
[ ] if slot is primary, image_url changes to new URL.
[ ] old PNG is deleted only after successful commit.
[ ] if conversion result is not smaller enough, image is skipped.
[ ] transparent PNG is flattened or skipped depending on flag.
[ ] invalid/missing file is reported as error but does not stop entire batch.
```

## 12.2. Manual QA

```text
[ ] Upload PNG to clear slot.
[ ] Run dry run.
[ ] Confirm dry run shows expected saving.
[ ] Run optimize.
[ ] Image still displays in location modal.
[ ] Image URL now ends with .webp.
[ ] DB record content_type is image/webp.
[ ] Old PNG file removed from media directory.
[ ] Primary image still works.
[ ] Export/import map still works after optimization.
```

---

# 13. Default settings

Use these defaults:

```json
{
  "target_format": "webp",
  "quality": 85,
  "method": 6,
  "source_content_types": ["image/png"],
  "replace_only_if_smaller": true,
  "min_saving_ratio": 0.15,
  "dry_run": true,
  "skip_transparent_png": false,
  "flatten_background": "#020617"
}
```

Reason:

```text
WebP q85 was the strongest tested option.
On the sample set:
36.33 MB PNG → 3.35 MB WebP q85
~90.8% saving
visual difference acceptable
```

---

# 14. Future options

Later add:

```text
- AVIF support;
- resize max dimension, e.g. 1920px width;
- per-slot optimization;
- per-location optimization;
- storage usage dashboard;
- duplicate image detection by perceptual hash;
- CDN/cache invalidation;
- background queue with persistent progress.
```

## Resize note

If current images are larger than UI needs, resizing may save even more than format conversion.

For example:

```text
2048px wide → 1600px wide
```

can reduce file size significantly.

But this PR should only do format optimization first, without resizing, to preserve composition and detail.

---

# 15. Acceptance criteria

```text
[ ] Debug UI has button for dry-run image optimization.
[ ] Debug UI has button to apply PNG → WebP q85 optimization.
[ ] Backend converts PNG location slot images to WebP q85.
[ ] DB LocationImage rows are updated.
[ ] location.image_slots are updated.
[ ] location.image_url remains synced to primary slot.
[ ] Old PNG files are removed only after successful save/commit.
[ ] Dry run changes nothing.
[ ] Optimization skips files if saving is below threshold.
[ ] Result report shows processed/converted/skipped/errors/bytes saved.
[ ] After optimization images still display in debug map and edit modal.
```

---

# 16. Minimal implementation path

For quickest safe PR:

```text
1. Add media_optimizer.py with WebP q85 encoder.
2. Add POST /zone-stalkers/contexts/{context_id}/images/optimize.
3. Support dry_run=true.
4. Process PNG LocationImage records for that context.
5. Update DB + state for converted files.
6. Delete old files after successful commit.
7. Add frontend button: “Оценить экономию”.
8. Add frontend button: “Оптимизировать PNG → WebP”.
9. Refresh map-static/projection after success.
10. Add backend tests for dry run and actual conversion.
```
