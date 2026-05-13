# Media Forge — отдельный проект для генерации звуков, апскейла и генерации изображений

## Назначение

`Media Forge` — новый проект/игровой модуль платформы, расположенный рядом с `zone_stalkers`, но не завязанный на конкретную игру. Его задача — быть внутренней фабрикой медиа-ассетов:

```text
- генерация ambient-звуков;
- генерация SFX;
- обработка, нормализация и экспорт audio;
- апскейл изображений;
- перекодирование изображений в WebP/JPEG;
- генерация и редактирование изображений;
- inpaint/outpaint;
- создание погодных/ночных вариантов изображений локаций;
- хранение job history, presets, outputs и metadata.
```

Проект должен быть развернут на той же платформе, что и `zone_stalkers`, иметь собственные backend routes, модели, UI, debug/admin tools и worker pipeline.

---

# 1. Цели проекта

## 1.1. Основные цели

`Media Forge` должен позволить:

```text
1. Создавать игровые звуки и ambient-слои через AI.
2. Апскейлить изображения локаций до FullHD/2K/4K.
3. Оптимизировать изображения в WebP q85.
4. Генерировать варианты изображений: туман, дождь, ночь, ночь+дождь.
5. Редактировать изображения через inpaint/outpaint.
6. Работать с batch jobs и отслеживать progress.
7. Сохранять все результаты как переиспользуемые ассеты.
8. Подключать результаты к другим проектам, например `zone_stalkers`.
```

## 1.2. Не цели MVP

На первом этапе не нужно:

```text
- realtime AI generation во время игры;
- полноценный DAW/аудиоредактор;
- продвинутый timeline для музыки;
- online marketplace ассетов;
- сложная система прав доступа;
- автоматическая публикация ассетов в production без review.
```

AI должен использоваться как **asset production tool**, а не как realtime gameplay dependency.

---

# 2. Размещение в репозитории

Предлагаемая структура:

```text
backend/app/games/media_forge/
  __init__.py
  router.py
  models.py
  schemas.py
  services/
    image_optimizer.py
    image_upscaler.py
    image_generator.py
    audio_generator.py
    audio_postprocess.py
    job_service.py
    storage_service.py
  workers/
    image_worker.py
    audio_worker.py
    gpu_worker.py
  presets/
    image_presets.py
    audio_presets.py
  tests/
    ...

frontend/src/games/media_forge/
  MediaForgePage.tsx
  api.ts
  types.ts
  components/
    JobList.tsx
    ImageLab.tsx
    AudioLab.tsx
    AssetGallery.tsx
    PresetPanel.tsx
    AudioPreview.tsx
    ImageCompare.tsx
```

Если в платформе есть общий механизм регистрации игр/модулей, `media_forge` должен быть зарегистрирован аналогично `zone_stalkers`.

---

# 3. Основная концепция

## 3.1. Не “игра”, а production lab

`Media Forge` логически является проектом типа:

```text
media production workspace
```

Он может использовать ту же платформенную инфраструктуру:

```text
- auth;
- context/project ownership;
- media storage;
- backend API;
- frontend routing;
- debug UI;
- jobs;
- database;
```

Но его “game state” — это не мир и NPC, а набор:

```text
- media assets;
- source files;
- generated variants;
- jobs;
- presets;
- batches;
- review decisions;
- links to consuming projects.
```

## 3.2. Основной workflow

```text
1. Пользователь загружает source image/audio или выбирает ассет из другого проекта.
2. Создаёт job: upscale / optimize / generate / inpaint / generate SFX.
3. Worker выполняет задачу.
4. Результаты сохраняются как media assets.
5. Пользователь сравнивает варианты.
6. Принимает лучший результат.
7. Экспортирует или привязывает ассет к проекту-потребителю.
```

---

# 4. Типы ассетов

## 4.1. Image asset

```json
{
  "id": "asset_img_...",
  "project_id": "...",
  "kind": "image",
  "category": "location",
  "source_type": "upload|generated|derived",
  "file_path": "media_forge/images/...",
  "public_url": "/media/media_forge/images/...",
  "content_type": "image/webp",
  "width": 1920,
  "height": 1080,
  "size_bytes": 512000,
  "metadata": {
    "prompt": "...",
    "negative_prompt": "...",
    "seed": 123,
    "model": "realesrgan-x2",
    "preset": "upscale_fullhd_webp85",
    "source_asset_id": "..."
  },
  "created_at": "...",
  "updated_at": "..."
}
```

## 4.2. Audio asset

```json
{
  "id": "asset_audio_...",
  "project_id": "...",
  "kind": "audio",
  "category": "sfx|ambient|music|voice",
  "subcategory": "footstep|anomaly|weapon|wind|bunker",
  "file_path": "media_forge/audio/...",
  "public_url": "/media/media_forge/audio/...",
  "content_type": "audio/ogg",
  "duration_ms": 1420,
  "sample_rate": 44100,
  "channels": 2,
  "size_bytes": 92000,
  "loop": false,
  "metadata": {
    "prompt": "single boot footstep on concrete",
    "model": "stable-audio-open",
    "seed": 42,
    "loudness_lufs": -18,
    "surface": "concrete"
  },
  "created_at": "...",
  "updated_at": "..."
}
```

---

# 5. Database models

## 5.1. `MediaForgeProject`

```python
class MediaForgeProject(Base):
    __tablename__ = "media_forge_projects"

    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUIDType, nullable=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    settings_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
```

## 5.2. `MediaAsset`

```python
class MediaAsset(Base):
    __tablename__ = "media_forge_assets"

    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    project_id = Column(UUIDType, ForeignKey("media_forge_projects.id"), nullable=False)

    kind = Column(String, nullable=False)          # image/audio
    category = Column(String, nullable=False)      # location/sfx/ambient/...
    subcategory = Column(String, nullable=True)

    source_type = Column(String, nullable=False)   # upload/generated/derived/imported
    source_asset_id = Column(UUIDType, nullable=True)

    filename = Column(String, nullable=False)
    content_type = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    size_bytes = Column(Integer, nullable=False)

    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)

    duration_ms = Column(Integer, nullable=True)
    sample_rate = Column(Integer, nullable=True)
    channels = Column(Integer, nullable=True)

    metadata_json = Column(JSON, nullable=False, default=dict)

    status = Column(String, nullable=False, default="ready")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
```

## 5.3. `MediaJob`

```python
class MediaJob(Base):
    __tablename__ = "media_forge_jobs"

    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    project_id = Column(UUIDType, ForeignKey("media_forge_projects.id"), nullable=False)

    job_type = Column(String, nullable=False)
    status = Column(String, nullable=False, default="queued")

    input_json = Column(JSON, nullable=False, default=dict)
    output_json = Column(JSON, nullable=False, default=dict)
    error_json = Column(JSON, nullable=True)

    progress_current = Column(Integer, nullable=False, default=0)
    progress_total = Column(Integer, nullable=False, default=0)

    created_asset_ids_json = Column(JSON, nullable=False, default=list)

    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
```

## 5.4. `MediaPreset`

```python
class MediaPreset(Base):
    __tablename__ = "media_forge_presets"

    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    project_id = Column(UUIDType, ForeignKey("media_forge_projects.id"), nullable=True)

    preset_type = Column(String, nullable=False)  # image/audio/upscale/optimize
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    config_json = Column(JSON, nullable=False, default=dict)
    is_system = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
```

---

# 6. Job types

## 6.1. Image jobs

```text
image_optimize
image_upscale_lanczos
image_upscale_ai
image_generate
image_img2img
image_inpaint
image_outpaint
image_make_weather_variant
image_batch_location_slots
```

## 6.2. Audio jobs

```text
audio_generate_sfx
audio_generate_ambient
audio_trim
audio_normalize
audio_loop_prepare
audio_batch_variants
audio_export_ogg
```

## 6.3. Utility jobs

```text
asset_import_from_project
asset_export_to_project
asset_delete_batch
asset_storage_report
asset_duplicate_scan
```

---

# 7. Image pipeline

## 7.1. Optimize

Default:

```json
{
  "target_format": "webp",
  "quality": 85,
  "replace_only_if_smaller": true,
  "min_saving_ratio": 0.15
}
```

Pipeline:

```text
source image
→ decode
→ optional flatten transparency
→ encode WebP q85
→ compare size
→ save if smaller
```

## 7.2. Simple upscale

Backend:

```text
Pillow Lanczos
```

Pipeline:

```text
source image
→ resize/fit/cover to target resolution
→ WebP q85
```

Presets:

```text
FullHD 1920×1080
2K 2560×1440
4K 3840×2160
```

## 7.3. AI upscale

Backends:

```text
realesrgan-ncnn-vulkan
Real-ESRGAN PyTorch
SwinIR optional
```

Recommended MVP backend:

```text
realesrgan-ncnn-vulkan
```

Reason:

```text
- easier deployment than PyTorch;
- can use Vulkan;
- works on NVIDIA/AMD/Intel GPUs;
- good for batch image upscaling.
```

Pipeline:

```text
source image
→ AI upscale x2/x4
→ final resize/crop to target
→ WebP q85
```

Presets:

```json
{
  "upscale_fullhd_fast": {
    "target_width": 1920,
    "target_height": 1080,
    "backend": "realesrgan_ncnn",
    "model": "realesr-general-x4v3",
    "output_format": "webp",
    "quality": 85
  },
  "upscale_2k_quality": {
    "target_width": 2560,
    "target_height": 1440,
    "backend": "realesrgan_ncnn",
    "model": "RealESRGAN_x2plus",
    "output_format": "webp",
    "quality": 85
  },
  "upscale_4k_heavy": {
    "target_width": 3840,
    "target_height": 2160,
    "backend": "realesrgan_ncnn",
    "model": "RealESRGAN_x4plus",
    "output_format": "webp",
    "quality": 85
  }
}
```

## 7.4. Image generation/editing

Backends:

```text
Stable Diffusion / SDXL / local diffusion backend
external API provider optional
```

Tasks:

```text
text-to-image
image-to-image
inpaint
outpaint
weather variant
night variant
rain variant
fog variant
```

For local RTX 3070/8GB:

```text
SD 1.5 768–1024 practical
FullHD via tiled workflow
final upscale to FullHD/2K
```

Pipeline for weather variant:

```text
source clear image
→ img2img prompt: "foggy abandoned industrial zone..."
→ preserve composition with low denoise
→ upscale/final resize
→ WebP q85
```

---

# 8. Audio pipeline

## 8.1. Audio generation backend

Potential backends:

```text
Stable Audio Open
AudioCraft / AudioGen
external API provider
```

Recommended MVP:

```text
support adapter interface first
implementation can be local Stable Audio Open or external provider later
```

Audio generation is not realtime. It should be background job.

## 8.2. SFX generation

Example request:

```json
{
  "job_type": "audio_generate_sfx",
  "category": "sfx",
  "subcategory": "footstep",
  "prompt": "single heavy boot footstep on dry concrete floor, close microphone, isolated foley sound effect, no music, no ambience, no reverb tail",
  "duration_seconds": 3,
  "variants": 8,
  "output_format": "ogg"
}
```

Postprocess:

```text
trim silence
split candidates if needed
normalize loudness
fade in/out
export OGG
write metadata
```

## 8.3. Ambient generation

Example request:

```json
{
  "job_type": "audio_generate_ambient",
  "category": "ambient",
  "subcategory": "bunker",
  "prompt": "dark underground bunker ambience, fluorescent light buzz, distant dripping water, low ventilation rumble, no voices, no melody",
  "duration_seconds": 30,
  "variants": 3,
  "loop": true,
  "output_format": "ogg"
}
```

Postprocess:

```text
normalize
optional loop smoothing
export OGG
metadata loop=true
```

## 8.4. Runtime usage

Generated audio should be used as prebuilt assets:

```text
game runtime plays OGG instantly
random pitch/gain for SFX
layered ambient mix for locations
```

Do not generate audio during gameplay.

---

# 9. Presets

## 9.1. Image presets

```python
SYSTEM_IMAGE_PRESETS = {
    "optimize_webp_q85": {
        "target_format": "webp",
        "quality": 85
    },
    "upscale_fullhd_webp85": {
        "target_width": 1920,
        "target_height": 1080,
        "upscale_backend": "realesrgan_ncnn",
        "output_format": "webp",
        "quality": 85
    },
    "variant_fog": {
        "task": "image_img2img",
        "prompt": "dense cold fog, abandoned zone atmosphere, preserve composition",
        "denoise_strength": 0.35
    },
    "variant_night_rain": {
        "task": "image_img2img",
        "prompt": "night rain, wet surfaces, moody blue darkness, preserve composition",
        "denoise_strength": 0.4
    }
}
```

## 9.2. Audio presets

```python
SYSTEM_AUDIO_PRESETS = {
    "footstep_concrete": {
        "duration_seconds": 3,
        "variants": 8,
        "prompt_template": "single heavy boot footstep on dry concrete floor, close microphone, isolated foley sound effect, no music"
    },
    "ambient_bunker": {
        "duration_seconds": 30,
        "variants": 3,
        "prompt_template": "dark underground bunker ambience, fluorescent light buzz, distant dripping water, low ventilation rumble, no voices, no melody"
    },
    "anomaly_electric": {
        "duration_seconds": 5,
        "variants": 5,
        "prompt_template": "electric anomaly crackle, dangerous sci-fi energy discharge, short game sound effect, no music"
    }
}
```

---

# 10. Backend API

## 10.1. Projects

```text
GET    /media-forge/projects
POST   /media-forge/projects
GET    /media-forge/projects/{project_id}
PATCH  /media-forge/projects/{project_id}
DELETE /media-forge/projects/{project_id}
```

## 10.2. Assets

```text
GET    /media-forge/projects/{project_id}/assets
POST   /media-forge/projects/{project_id}/assets/upload
GET    /media-forge/assets/{asset_id}
PATCH  /media-forge/assets/{asset_id}
DELETE /media-forge/assets/{asset_id}
```

Filters:

```text
kind=image/audio
category=sfx/ambient/location
subcategory=footstep/concrete/bunker
source_type=upload/generated/derived
```

## 10.3. Jobs

```text
GET    /media-forge/projects/{project_id}/jobs
POST   /media-forge/projects/{project_id}/jobs
GET    /media-forge/jobs/{job_id}
POST   /media-forge/jobs/{job_id}/cancel
POST   /media-forge/jobs/{job_id}/retry
```

## 10.4. Specialized convenience endpoints

```text
POST /media-forge/projects/{project_id}/images/optimize
POST /media-forge/projects/{project_id}/images/upscale
POST /media-forge/projects/{project_id}/images/generate
POST /media-forge/projects/{project_id}/images/inpaint

POST /media-forge/projects/{project_id}/audio/generate-sfx
POST /media-forge/projects/{project_id}/audio/generate-ambient
POST /media-forge/projects/{project_id}/audio/postprocess
```

---

# 11. Worker architecture

## 11.1. Why workers

Image/audio generation can take seconds or minutes.

Do not run heavy generation in normal request lifecycle.

Use:

```text
request → create job → return job_id → worker processes → UI polls progress
```

## 11.2. MVP worker

If no queue system exists:

```text
in-process background task
single worker
in-memory job registry + DB persisted status
```

## 11.3. Better worker

Later:

```text
Redis queue / Celery / RQ / custom asyncio worker
GPU worker process
```

## 11.4. GPU locking

Only one heavy GPU job should run at once by default.

```python
GPU_JOB_LOCK = asyncio.Lock()
```

or DB/Redis lock:

```text
media_forge:gpu_lock
```

This prevents VRAM OOM.

---

# 12. Storage

## 12.1. File layout

```text
media/
  media_forge/
    projects/
      <project_id>/
        images/
          source/
          generated/
          optimized/
          upscaled/
          variants/
        audio/
          source/
          generated/
          normalized/
          exports/
```

Example:

```text
media/media_forge/projects/<project_id>/images/upscaled/<asset_id>.webp
media/media_forge/projects/<project_id>/audio/generated/<asset_id>.ogg
```

## 12.2. Safe write

Always:

```text
write new file
create/update DB row
commit DB
then delete old file if replacing
```

For errors:

```text
rollback DB
delete newly written file
leave old file untouched
```

---

# 13. Frontend UI

## 13.1. Main page

```text
MediaForgePage
  ├── Project selector
  ├── Tabs
  │   ├── Asset Gallery
  │   ├── Image Lab
  │   ├── Audio Lab
  │   ├── Jobs
  │   └── Presets
```

## 13.2. Asset Gallery

Features:

```text
- grid/list view;
- filters by kind/category/subcategory;
- preview image/audio;
- compare variants;
- mark accepted/rejected;
- export to project.
```

## 13.3. Image Lab

Modes:

```text
- optimize image;
- bulk optimize;
- upscale;
- generate;
- inpaint/outpaint;
- weather variant generation.
```

Controls:

```text
source asset
preset
target size
format
quality
prompt
negative prompt
seed
variants count
dry run
start job
```

## 13.4. Audio Lab

Modes:

```text
- generate SFX;
- generate ambient;
- normalize;
- loop prepare;
- export OGG.
```

Controls:

```text
category
subcategory
prompt
duration
variants count
loop flag
normalize target
start job
```

## 13.5. Jobs tab

```text
job list
status
progress
created assets
errors
cancel/retry
```

---

# 14. Integration with Zone Stalkers

`Media Forge` should not depend on `zone_stalkers`, but `zone_stalkers` can consume its assets.

## 14.1. Export image to location slot

Action:

```text
Export to Zone Stalkers location slot
```

Payload:

```json
{
  "target_project": "zone_stalkers",
  "context_id": "...",
  "location_id": "loc_A",
  "slot": "rain",
  "asset_id": "..."
}
```

This should copy/reuse media file and update:

```text
LocationImage row
location.image_slots[slot]
location.image_url if primary
```

## 14.2. Export audio to game asset

Future `zone_stalkers` audio metadata:

```json
{
  "audio_asset_id": "...",
  "category": "ambient",
  "location_id": "loc_A",
  "weather": "rain",
  "time_of_day": "night",
  "loop": true
}
```

---

# 15. Resource profiles

## 15.1. No GPU

Allowed:

```text
image optimize
Pillow resize
audio trim/normalize/export
```

Not recommended:

```text
AI image generation
AI audio generation
AI upscale
```

## 15.2. GTX 1660 6GB

Reasonable:

```text
image upscale FullHD/2K with tiling
small AI image edit at 512–768
short audio SFX generation if model fits
```

Not ideal:

```text
large diffusion jobs
parallel GPU jobs
long audio generation
```

## 15.3. RTX 3070 8GB

Reasonable:

```text
FullHD/2K image upscale
selective 4K upscale
SD 1.5 image edit at 768–1024
audio SFX/ambient generation with compatible model
```

Still avoid:

```text
parallel heavy GPU jobs
mass 4K creative diffusion
realtime generation
```

---

# 16. Configuration

Add config:

```python
MEDIA_FORGE_ENABLED = True
MEDIA_FORGE_MEDIA_ROOT = "media/media_forge"

MEDIA_FORGE_GPU_ENABLED = True
MEDIA_FORGE_MAX_GPU_WORKERS = 1
MEDIA_FORGE_DEFAULT_IMAGE_FORMAT = "webp"
MEDIA_FORGE_DEFAULT_IMAGE_QUALITY = 85

MEDIA_FORGE_REALESRGAN_BIN = "/opt/realesrgan-ncnn-vulkan/realesrgan-ncnn-vulkan"
MEDIA_FORGE_REALESRGAN_MODELS_DIR = "/opt/realesrgan-ncnn-vulkan/models"

MEDIA_FORGE_AUDIO_BACKEND = "stable_audio_open"
MEDIA_FORGE_AUDIO_MODEL_PATH = "/models/stable-audio-open"
```

---

# 17. Security and safety

## 17.1. File validation

Validate uploads:

```text
MIME type
extension
file size
image dimensions
audio duration
```

Limits:

```text
max image upload: 50 MB
max audio upload: 100 MB
max generated duration: 47 sec for MVP
```

## 17.2. Path safety

Never trust filenames.

Use:

```text
uuid filenames
safe extension from detected content type
no user-provided path segments
```

## 17.3. Prompt logging

Store prompts in metadata for reproducibility.

## 17.4. Copyright

Add UI reminder:

```text
Do not upload copyrighted source material unless you have rights to use and transform it.
```

---

# 18. Testing

## 18.1. Backend tests

```text
test_create_media_project
test_upload_image_asset
test_upload_audio_asset
test_image_optimize_webp_q85
test_image_upscale_lanczos
test_job_create_and_complete
test_job_failure_records_error
test_safe_file_replace
test_asset_delete_removes_file
test_preset_list
```

## 18.2. Worker tests

```text
test_worker_picks_queued_job
test_worker_updates_progress
test_worker_respects_gpu_lock
test_worker_can_cancel_job
```

## 18.3. Frontend manual QA

```text
[ ] Create Media Forge project.
[ ] Upload image.
[ ] Optimize to WebP q85.
[ ] Upscale to FullHD.
[ ] Compare source/result.
[ ] Upload audio.
[ ] Normalize audio.
[ ] Generate SFX job.
[ ] Preview generated result.
[ ] Accept/reject asset.
[ ] Export image to Zone Stalkers location slot.
```

---

# 19. MVP scope

## PR 1 — project skeleton

```text
backend/app/games/media_forge
router
models
schemas
asset upload
asset listing
frontend page
asset gallery
```

## PR 2 — image optimize

```text
WebP q85 optimize
Pillow resize
safe replace
jobs
frontend Image Lab basic
```

## PR 3 — image upscale

```text
Lanczos upscale
Real-ESRGAN adapter interface
optional NCNN backend
job progress
```

## PR 4 — audio asset pipeline

```text
audio upload
preview
trim/normalize/export OGG
metadata
```

## PR 5 — AI audio generation

```text
audio generation adapter
SFX/ambient prompts
variants
postprocess
review UI
```

## PR 6 — AI image generation/editing

```text
image generation adapter
img2img/inpaint/outpaint
weather presets
variant generation
```

## PR 7 — Zone Stalkers integration

```text
export Media Forge image asset to Zone Stalkers location slot
export audio asset metadata to Zone Stalkers
```

---

# 20. Acceptance criteria for MVP

```text
[ ] Media Forge appears as separate project/module in platform UI.
[ ] User can create/open a Media Forge project.
[ ] User can upload image/audio assets.
[ ] Assets are stored with metadata and previewable.
[ ] User can create image optimize job.
[ ] WebP q85 outputs are generated and saved.
[ ] User can create simple upscale job.
[ ] Jobs have status/progress/error output.
[ ] Generated assets appear in gallery.
[ ] Files are written safely.
[ ] Deleting asset removes DB row and file.
```

---

# 21. Long-term vision

`Media Forge` should become a reusable internal tool for all game projects on the platform.

Target capabilities:

```text
- procedural asset production;
- batch optimization;
- AI-assisted upscaling;
- AI-assisted SFX generation;
- AI-assisted ambient generation;
- AI image editing;
- asset review workflow;
- export to any game module;
- reproducible presets;
- storage usage analytics.
```

The module should be independent from `zone_stalkers`, but `zone_stalkers` should be the first consumer.
