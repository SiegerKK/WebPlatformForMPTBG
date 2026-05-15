/**
 * Modals — LocationModal (edit/create location) and SpawnMutantModal.
 */
import { useEffect, useMemo, useState } from 'react';
import { s } from './styles';
import {
  LOCATION_IMAGE_GROUP_LABELS,
  LOCATION_IMAGE_GROUP_SLOT_MAP,
  getEnabledImageGroups,
  getImageSlotIcon,
  getImageSlotLabel,
  getImageSlotUrl,
  type LocationImageGroup,
  type LocationImageProfile,
  type LocationImageRef,
  type LocationImageSlotsV2,
} from './types';

// ─── Terrain type options ─────────────────────────────────────────────────────

export const TERRAIN_TYPES = [
  'plain', 'hills', 'swamp', 'field_camp', 'slag_heaps', 'bridge',
  'industrial', 'buildings', 'military_buildings', 'hamlet', 'farm',
  'dungeon', 'x_lab', 'tunnel', 'scientific_bunker',
] as const;

export const TERRAIN_TYPE_LABELS: Record<string, string> = {
  plain: 'Равнина',
  hills: 'Холмы',
  swamp: 'Болото',
  field_camp: 'Полевой лагерь',
  slag_heaps: 'Терриконы',
  bridge: 'Мост',
  industrial: 'Промзона',
  buildings: 'Здания',
  military_buildings: 'Воен. здания',
  hamlet: 'Хутор',
  farm: 'Ферма',
  dungeon: 'Подземелья',
  tunnel: 'Туннель',
  x_lab: 'Лаборатория X',
  scientific_bunker: 'Науч. бункер',
};

export const DOMINANT_ANOMALY_OPTIONS = [
  '', 'chemical', 'electric', 'gravitational', 'thermal', 'radioactive',
] as const;

// ─── Mutant type options ──────────────────────────────────────────────────────

export const MUTANT_TYPE_OPTIONS = [
  'blind_dog', 'flesh', 'zombie', 'bloodsucker', 'psi_controller',
] as const;

export const MUTANT_TYPE_LABELS: Record<string, string> = {
  blind_dog: 'Blind Dog',
  flesh: 'Flesh',
  zombie: 'Zombie',
  bloodsucker: 'Bloodsucker',
  psi_controller: 'Psi-Controller',
};

// ─── LocationModal ────────────────────────────────────────────────────────────

export interface LocationSaveData {
  name: string;
  terrainType: string;
  anomalyActivity: number;
  dominantAnomalyType: string;
  region: string;
  exitZone: boolean;
  imageProfile: LocationImageProfile;
}

function buildFullSlotsV2(initial?: LocationImageSlotsV2): LocationImageSlotsV2 {
  const out: LocationImageSlotsV2 = {};
  for (const group of Object.keys(LOCATION_IMAGE_GROUP_SLOT_MAP) as LocationImageGroup[]) {
    const groupOut: Record<string, string | null> = {};
    for (const slot of LOCATION_IMAGE_GROUP_SLOT_MAP[group]) {
      groupOut[slot] = (initial?.[group] as Record<string, string | null | undefined> | undefined)?.[slot] ?? null;
    }
    (out as Record<string, Record<string, string | null>>)[group] = groupOut;
  }
  return out;
}

// ─── LocationModal ────────────────────────────────────────────────────────────

export function LocationModal({
  mode,
  initialName = '',
  initialTerrainType = 'plain',
  initialAnomalyActivity = 0,
  initialDominantAnomalyType = '',
  initialRegion = '',
  initialExitZone = false,
  initialImageSlotsV2,
  initialPrimaryImageRef = null,
  initialImageProfile,
  initialImageUrl = null,
  regions,
  locId,
  onUploadImageSlot,
  onDeleteImageSlot,
  onSetPrimaryImageRef,
  onPatchImageProfile,
  onClose,
  onSave,
}: {
  mode: 'edit' | 'create';
  initialName?: string;
  initialTerrainType?: string;
  initialAnomalyActivity?: number;
  initialDominantAnomalyType?: string;
  initialRegion?: string;
  initialExitZone?: boolean;
  initialImageSlotsV2?: LocationImageSlotsV2;
  initialPrimaryImageRef?: LocationImageRef | null;
  initialImageProfile?: LocationImageProfile;
  initialImageUrl?: string | null;
  regions?: Record<string, { name: string; colorIndex: number }>;
  locId?: string;
  onUploadImageSlot?: (group: LocationImageGroup, slot: string, file: File) => Promise<{
    image_slots_v2?: LocationImageSlotsV2;
    primary_image_ref?: LocationImageRef | null;
    image_profile?: LocationImageProfile;
  } | void>;
  onDeleteImageSlot?: (group: LocationImageGroup, slot: string) => Promise<{
    image_slots_v2?: LocationImageSlotsV2;
    primary_image_ref?: LocationImageRef | null;
    image_profile?: LocationImageProfile;
  } | void>;
  onSetPrimaryImageRef?: (ref: LocationImageRef) => Promise<void>;
  onPatchImageProfile?: (profile: LocationImageProfile) => Promise<{
    image_slots_v2?: LocationImageSlotsV2;
    primary_image_ref?: LocationImageRef | null;
    image_profile?: LocationImageProfile;
  } | void>;
  onClose: () => void;
  onSave: (data: LocationSaveData) => Promise<void>;
}) {
  const [name, setName] = useState(initialName);
  const [terrainType, setTerrainType] = useState(initialTerrainType);
  const [anomalyActivity, setAnomalyActivity] = useState(initialAnomalyActivity);
  const [dominantAnomalyType, setDominantAnomalyType] = useState(initialDominantAnomalyType);
  const [region, setRegion] = useState(initialRegion);
  const [exitZone, setExitZone] = useState(initialExitZone);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [slotBusy, setSlotBusy] = useState<Record<string, boolean>>({});
  const canEditImages = mode === 'edit' && !!locId;

  const [imageProfile, setImageProfile] = useState<LocationImageProfile>({
    is_anomalous: Boolean(initialImageProfile?.is_anomalous),
    is_psi: Boolean(initialImageProfile?.is_psi),
    is_underground: Boolean(initialImageProfile?.is_underground),
  });
  const [imageSlotsV2, setImageSlotsV2] = useState<LocationImageSlotsV2>(buildFullSlotsV2(initialImageSlotsV2));
  const [primaryImageRef, setPrimaryImageRef] = useState<LocationImageRef | null>(initialPrimaryImageRef ?? null);

  useEffect(() => {
    setName(initialName);
    setTerrainType(initialTerrainType);
    setAnomalyActivity(initialAnomalyActivity);
    setDominantAnomalyType(initialDominantAnomalyType);
    setRegion(initialRegion);
    setExitZone(initialExitZone);
    setImageProfile({
      is_anomalous: Boolean(initialImageProfile?.is_anomalous),
      is_psi: Boolean(initialImageProfile?.is_psi),
      is_underground: Boolean(initialImageProfile?.is_underground),
    });
    setImageSlotsV2(buildFullSlotsV2(initialImageSlotsV2));
    setPrimaryImageRef(initialPrimaryImageRef ?? null);
    setSlotBusy({});
  }, [
    initialName,
    initialTerrainType,
    initialAnomalyActivity,
    initialDominantAnomalyType,
    initialRegion,
    initialExitZone,
    initialImageProfile,
    initialImageSlotsV2,
    initialPrimaryImageRef,
    locId,
  ]);

  const enabledGroups = getEnabledImageGroups(imageProfile);

  const primaryImageUrl = useMemo(() => {
    const direct = getImageSlotUrl(imageSlotsV2, primaryImageRef);
    if (direct) return direct;
    for (const group of enabledGroups) {
      for (const slot of LOCATION_IMAGE_GROUP_SLOT_MAP[group]) {
        const url = (imageSlotsV2[group] as Record<string, string | null | undefined> | undefined)?.[slot] ?? null;
        if (url) return url;
      }
    }
    return initialImageUrl;
  }, [enabledGroups, imageSlotsV2, initialImageUrl, primaryImageRef]);

  const applyServerImageState = (next?: {
    image_slots_v2?: LocationImageSlotsV2;
    primary_image_ref?: LocationImageRef | null;
    image_profile?: LocationImageProfile;
  } | void) => {
    if (!next) return;
    if (next.image_slots_v2) setImageSlotsV2(buildFullSlotsV2(next.image_slots_v2));
    if (Object.prototype.hasOwnProperty.call(next, 'primary_image_ref')) setPrimaryImageRef(next.primary_image_ref ?? null);
    if (next.image_profile) {
      setImageProfile({
        is_anomalous: Boolean(next.image_profile.is_anomalous),
        is_psi: Boolean(next.image_profile.is_psi),
        is_underground: Boolean(next.image_profile.is_underground),
      });
    }
  };

  const withSlotBusy = async (key: string, action: () => Promise<void>) => {
    setSlotBusy((prev) => ({ ...prev, [key]: true }));
    setErr(null);
    try {
      await action();
    } finally {
      setSlotBusy((prev) => ({ ...prev, [key]: false }));
    }
  };

  const handleUploadSlot = async (group: LocationImageGroup, slot: string, file: File) => {
    if (!canEditImages || !onUploadImageSlot) return;
    const key = `${group}:${slot}`;
    try {
      await withSlotBusy(key, async () => {
        const next = await onUploadImageSlot(group, slot, file);
        applyServerImageState(next);
      });
    } catch (e: unknown) {
      setErr((e as { message?: string })?.message ?? 'Upload failed');
    }
  };

  const handleDeleteSlot = async (group: LocationImageGroup, slot: string) => {
    if (!canEditImages || !onDeleteImageSlot) return;
    const key = `${group}:${slot}`;
    const url = (imageSlotsV2[group] as Record<string, string | null | undefined> | undefined)?.[slot] ?? null;
    if (!url) return;
    try {
      await withSlotBusy(key, async () => {
        const next = await onDeleteImageSlot(group, slot);
        applyServerImageState(next);
      });
    } catch (e: unknown) {
      setErr((e as { message?: string })?.message ?? 'Delete failed');
    }
  };

  const handleSetPrimary = async (group: LocationImageGroup, slot: string) => {
    if (!canEditImages || !onSetPrimaryImageRef) return;
    const url = (imageSlotsV2[group] as Record<string, string | null | undefined> | undefined)?.[slot] ?? null;
    if (!url) return;
    const key = `${group}:${slot}`;
    try {
      await withSlotBusy(key, async () => {
        await onSetPrimaryImageRef({ group, slot });
        setPrimaryImageRef({ group, slot });
      });
    } catch (e: unknown) {
      setErr((e as { message?: string })?.message ?? 'Set primary failed');
    }
  };

  const handleDownloadSlot = async (group: LocationImageGroup, slot: string) => {
    const url = (imageSlotsV2[group] as Record<string, string | null | undefined> | undefined)?.[slot] ?? null;
    if (!url) return;
    const key = `${group}:${slot}`;
    try {
      await withSlotBusy(key, async () => {
        const res = await fetch(url, { credentials: 'include' });
        if (!res.ok) throw new Error(`Failed to download image: ${res.status}`);
        const blob = await res.blob();
        const extFromType = blob.type === 'image/png' ? '.png'
          : blob.type === 'image/webp' ? '.webp'
            : blob.type === 'image/gif' ? '.gif'
              : '.jpg';
        const extFromUrlMatch = url.match(/\.(jpg|jpeg|png|webp|gif)(?:$|\?)/i);
        const ext = extFromUrlMatch ? `.${extFromUrlMatch[1].toLowerCase() === 'jpeg' ? 'jpg' : extFromUrlMatch[1].toLowerCase()}` : extFromType;
        const objectUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = objectUrl;
        a.download = `${locId ?? 'location'}_${group}_${slot}${ext}`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(objectUrl);
      });
    } catch (e: unknown) {
      setErr((e as { message?: string })?.message ?? 'Download failed');
    }
  };

  const handleProfileToggle = async (key: keyof LocationImageProfile, checked: boolean) => {
    const nextProfile: LocationImageProfile = { ...imageProfile, [key]: checked };
    setImageProfile(nextProfile);
    if (!canEditImages || !onPatchImageProfile) return;
    try {
      const next = await onPatchImageProfile(nextProfile);
      applyServerImageState(next);
    } catch (e: unknown) {
      setErr((e as { message?: string })?.message ?? 'Profile patch failed');
    }
  };

  const handleSubmit = async () => {
    const trimmed = name.trim();
    if (!trimmed) { setErr('Name cannot be empty'); return; }
    setSaving(true); setErr(null);
    try {
      await onSave({
        name: trimmed,
        terrainType,
        anomalyActivity,
        dominantAnomalyType,
        region,
        exitZone,
        imageProfile,
      });
      onClose();
    } catch (e: unknown) {
      setErr((e as { message?: string })?.message ?? 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={s.modalOverlay} onMouseDown={onClose}>
      <div style={s.locationModal} onMouseDown={(e) => e.stopPropagation()}>
        <h3 style={{ margin: '0 0 12px', color: '#f8fafc', fontSize: '1rem' }}>
          {mode === 'edit' ? '✏ Edit Location' : '➕ New Location'}
        </h3>
        {locId && (
          <div style={{ color: '#475569', fontSize: '0.65rem', marginBottom: 10 }}>ID: {locId}</div>
        )}

        <label style={s.modalLabel}>Name</label>
        <input
          style={s.modalInput}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Location name"
          autoFocus
        />

        <label style={s.modalLabel}>Terrain type</label>
        <select
          style={s.modalInput}
          value={terrainType}
          onChange={(e) => setTerrainType(e.target.value)}
        >
          {TERRAIN_TYPES.map((t) => (
            <option key={t} value={t}>{TERRAIN_TYPE_LABELS[t] ?? t}</option>
          ))}
        </select>

        <label style={s.modalLabel}>Anomaly activity: {anomalyActivity}</label>
        <input
          type="range"
          min={0}
          max={10}
          step={1}
          value={anomalyActivity}
          onChange={(e) => setAnomalyActivity(Number(e.target.value))}
          style={{ width: '100%', accentColor: '#a855f7', marginBottom: 10 }}
        />

        <label style={s.modalLabel}>Dominant anomaly type</label>
        <select
          style={s.modalInput}
          value={dominantAnomalyType}
          onChange={(e) => setDominantAnomalyType(e.target.value)}
        >
          {DOMINANT_ANOMALY_OPTIONS.map((t) => (
            <option key={t} value={t}>{t === '' ? '— none —' : t}</option>
          ))}
        </select>

        {regions && (
          <>
            <label style={s.modalLabel}>Регион</label>
            <select
              style={s.modalInput}
              value={region}
              onChange={(e) => setRegion(e.target.value)}
            >
              <option value="">— None —</option>
              {Object.entries(regions).map(([id, r]) => (
                <option key={id} value={id}>{r.name}</option>
              ))}
            </select>
          </>
        )}

        <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', color: '#f8fafc', fontSize: '0.8rem', margin: '10px 0 4px' }}>
          <input
            type="checkbox"
            checked={exitZone}
            onChange={(e) => setExitZone(e.target.checked)}
            style={{ width: 16, height: 16, accentColor: '#22d3ee', cursor: 'pointer' }}
          />
          🚪 Выход из Зоны
        </label>

        <div style={{ marginTop: 8, borderTop: '1px solid #1e293b', paddingTop: 10 }}>
          <label style={{ ...s.modalLabel, marginBottom: 6, display: 'block' }}>🧬 Профиль изображений</label>
          <div style={{ display: 'grid', gap: 6, gridTemplateColumns: 'repeat(3, minmax(120px, 1fr))' }}>
            {[
              { key: 'is_anomalous' as const, label: 'is_anomalous' },
              { key: 'is_psi' as const, label: 'is_psi' },
              { key: 'is_underground' as const, label: 'is_underground' },
            ].map((item) => (
              <label key={item.key} style={{ display: 'flex', alignItems: 'center', gap: 6, color: '#cbd5e1', fontSize: '0.75rem' }}>
                <input
                  type="checkbox"
                  checked={Boolean(imageProfile[item.key])}
                  onChange={(e) => void handleProfileToggle(item.key, e.target.checked)}
                  style={{ accentColor: '#60a5fa' }}
                />
                {item.label}
              </label>
            ))}
          </div>
        </div>

        {/* ── Grouped image slots editor ───────────────────────────────────── */}
        <div style={{ marginTop: 12 }}>
          <label style={s.modalLabel}>🖼 Изображения локации (v2)</label>
          {mode === 'create' && (
            <div style={{ color: '#64748b', fontSize: '0.72rem', marginBottom: 8 }}>
              Сначала создайте локацию, затем добавьте изображения в режиме редактирования.
            </div>
          )}
          {mode === 'edit' && (
            <>
              <div style={{
                width: '100%',
                borderRadius: 8,
                border: '1px solid #1e3a5f',
                background: '#020617',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: primaryImageUrl ? '#f8fafc' : '#334155',
                fontSize: '0.75rem',
                marginBottom: 8,
                overflow: 'hidden',
              }}>
                {primaryImageUrl ? (
                  <img src={primaryImageUrl} alt={name || 'location'} style={{ width: '100%', height: 'auto', objectFit: 'contain', display: 'block' }} />
                ) : (
                  'Нет изображения'
                )}
              </div>

              <div style={{ display: 'grid', gap: 12 }}>
                {enabledGroups.map((group) => (
                  <div key={group}>
                    <div style={{ color: '#93c5fd', fontSize: '0.74rem', marginBottom: 6 }}>
                      {LOCATION_IMAGE_GROUP_LABELS[group]} · {group}
                    </div>
                    <div style={{ display: 'grid', gap: 8, gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))' }}>
                      {LOCATION_IMAGE_GROUP_SLOT_MAP[group].map((slot) => {
                        const url = (imageSlotsV2[group] as Record<string, string | null | undefined> | undefined)?.[slot] ?? null;
                        const isPrimary = primaryImageRef?.group === group && primaryImageRef?.slot === slot;
                        const busy = Boolean(slotBusy[`${group}:${slot}`]);
                        return (
                          <div key={`${group}:${slot}`} style={{ border: '1px solid #1e293b', borderRadius: 6, padding: 8, background: '#0b1220' }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 6, marginBottom: 6, color: '#cbd5e1', fontSize: '0.7rem' }}>
                              <span>{getImageSlotIcon(group, slot)} {getImageSlotLabel(group, slot)}</span>
                              <span style={{ color: isPrimary ? '#60a5fa' : '#475569' }}>{isPrimary ? '★ primary' : ''}</span>
                            </div>
                            <div style={{
                              width: '100%',
                              height: 80,
                              borderRadius: 6,
                              border: '1px solid #1e3a5f',
                              background: '#020617',
                              marginBottom: 6,
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              overflow: 'hidden',
                              color: '#334155',
                              fontSize: '0.68rem',
                            }}>
                              {url ? <img src={url} alt={`${group}:${slot}`} style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : 'пусто'}
                            </div>
                            <div style={{ display: 'grid', gap: 4 }}>
                              <label style={{ ...s.spawnBtn, cursor: canEditImages && !busy ? 'pointer' : 'default', opacity: canEditImages && !busy ? 1 : 0.6 }}>
                                📤 Загрузить
                                <input
                                  type="file"
                                  accept="image/jpeg,image/png,image/webp,image/gif"
                                  style={{ display: 'none' }}
                                  disabled={!canEditImages || busy}
                                  onChange={(e) => {
                                    const file = e.target.files?.[0];
                                    if (file) void handleUploadSlot(group, slot, file);
                                    e.currentTarget.value = '';
                                  }}
                                />
                              </label>
                              <button
                                type="button"
                                style={s.spawnBtn}
                                disabled={!url || busy}
                                onClick={() => void handleDownloadSlot(group, slot)}
                              >
                                ⬇ Скачать
                              </button>
                              <button
                                type="button"
                                style={{ ...s.spawnBtn, color: '#ef4444', borderColor: '#7f1d1d' }}
                                disabled={!canEditImages || !url || busy}
                                onClick={() => void handleDeleteSlot(group, slot)}
                              >
                                🗑 Удалить
                              </button>
                              <button
                                type="button"
                                style={{ ...s.spawnBtn, color: '#93c5fd', borderColor: '#1d4ed8' }}
                                disabled={!canEditImages || !url || isPrimary || busy}
                                onClick={() => void handleSetPrimary(group, slot)}
                              >
                                ★ Primary
                              </button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>

        {err && <div style={{ color: '#ef4444', fontSize: '0.72rem', marginTop: 6 }}>{err}</div>}

        <div style={{ display: 'flex', gap: 8, marginTop: 14, justifyContent: 'flex-end' }}>
          <button style={s.modalCancelBtn} onClick={onClose} disabled={saving}>Cancel</button>
          <button style={s.modalSaveBtn} onClick={handleSubmit} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── SpawnMutantModal ─────────────────────────────────────────────────────────

export function SpawnMutantModal({
  onClose,
  onSave,
}: {
  onClose: () => void;
  onSave: (mutantType: string) => Promise<void>;
}) {
  const [mutantType, setMutantType] = useState<string>(MUTANT_TYPE_OPTIONS[0]);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const handleSubmit = async () => {
    setSaving(true); setErr(null);
    try {
      await onSave(mutantType);
    } catch (e: unknown) {
      setErr((e as { message?: string })?.message ?? 'Spawn failed');
      setSaving(false);
    }
  };

  return (
    <div style={s.modalOverlay} onMouseDown={onClose}>
      <div style={s.modal} onMouseDown={(e) => e.stopPropagation()}>
        <h3 style={{ margin: '0 0 12px', color: '#f8fafc', fontSize: '1rem' }}>☣️ Spawn Mutant</h3>
        <label style={s.modalLabel}>Тип мутанта</label>
        <select
          style={s.modalInput}
          value={mutantType}
          onChange={(e) => setMutantType(e.target.value)}
          autoFocus
        >
          {MUTANT_TYPE_OPTIONS.map((t) => (
            <option key={t} value={t}>{MUTANT_TYPE_LABELS[t] ?? t}</option>
          ))}
        </select>
        {err && <div style={{ color: '#ef4444', fontSize: '0.72rem', marginTop: 6 }}>{err}</div>}
        <div style={{ display: 'flex', gap: 8, marginTop: 14, justifyContent: 'flex-end' }}>
          <button style={s.modalCancelBtn} onClick={onClose} disabled={saving}>Cancel</button>
          <button style={s.modalSaveBtn} onClick={handleSubmit} disabled={saving}>
            {saving ? 'Spawning…' : 'Spawn'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── SpawnArtifactModal ───────────────────────────────────────────────────────

export const ARTIFACT_TYPE_OPTIONS = [
  'soul', 'stone_flower', 'flash', 'fireball', 'gravi', 'moonlight', 'battery', 'urchin',
] as const;

export const ARTIFACT_TYPE_LABELS: Record<string, string> = {
  soul:         'Soul (душа)',
  stone_flower: 'Stone Flower (каменный цветок)',
  flash:        'Flash (вспышка)',
  fireball:     'Fireball (огненный шар)',
  gravi:        'Gravi (гравий)',
  moonlight:    'Moonlight (лунный свет)',
  battery:      'Battery (батарея)',
  urchin:       'Urchin (ёж)',
};

// ─── SpawnItemModal ───────────────────────────────────────────────────────────

type ItemOption = { value: string; label: string; category: string };

export const ITEM_TYPE_OPTIONS: ItemOption[] = [
  // Medical
  { value: 'bandage',       label: 'Бинт',                   category: '💊 Медицина' },
  { value: 'medkit',        label: 'Аптечка',                 category: '💊 Медицина' },
  { value: 'army_medkit',   label: 'Военная аптечка',         category: '💊 Медицина' },
  { value: 'stimpack',      label: 'Стимпак',                 category: '💊 Медицина' },
  { value: 'morphine',      label: 'Морфин',                  category: '💊 Медицина' },
  { value: 'antirad',       label: 'Антирад',                 category: '💊 Медицина' },
  { value: 'rad_cure',      label: 'Рад-Пурге',               category: '💊 Медицина' },
  // Weapons
  { value: 'pistol',        label: 'Пистолет ПМ',             category: '🔫 Оружие' },
  { value: 'shotgun',       label: 'Обрез ТОЗ-34',            category: '🔫 Оружие' },
  { value: 'ak74',          label: 'АК-74',                   category: '🔫 Оружие' },
  { value: 'pkm',           label: 'ПКМ (пулемёт)',           category: '🔫 Оружие' },
  { value: 'svu_svd',       label: 'СВД (снайперская)',        category: '🔫 Оружие' },
  // Armor
  { value: 'leather_jacket',label: 'Кожаная куртка',          category: '🛡 Броня' },
  { value: 'stalker_suit',  label: 'Комбинезон сталкера',      category: '🛡 Броня' },
  { value: 'combat_armor',  label: 'Боевой бронежилет',        category: '🛡 Броня' },
  { value: 'seva_suit',     label: 'Костюм СЕВА',              category: '🛡 Броня' },
  { value: 'exoskeleton',   label: 'Экзоскелет',               category: '🛡 Броня' },
  // Ammo
  { value: 'ammo_9mm',      label: 'Патроны 9х18 (20 шт.)',    category: '🔧 Патроны' },
  { value: 'ammo_12gauge',  label: 'Дробь 12 кал. (10 шт.)',   category: '🔧 Патроны' },
  { value: 'ammo_545',      label: 'Патроны 5.45х39 (30 шт.)', category: '🔧 Патроны' },
  { value: 'ammo_762',      label: 'Патроны 7.62х54R (20 шт.)',category: '🔧 Патроны' },
  // Consumables
  { value: 'bread',         label: 'Буханка хлеба',            category: '🍞 Еда и вода' },
  { value: 'canned_food',   label: 'Тушёнка',                  category: '🍞 Еда и вода' },
  { value: 'military_ration',label:'Сухой паёк',               category: '🍞 Еда и вода' },
  { value: 'water',         label: 'Вода (0.5л)',               category: '🍞 Еда и вода' },
  { value: 'purified_water',label: 'Очищенная вода (1л)',       category: '🍞 Еда и вода' },
  { value: 'energy_drink',  label: 'Энергетик',                 category: '🍞 Еда и вода' },
  { value: 'vodka',         label: 'Водка',                     category: '🍞 Еда и вода' },
  { value: 'glucose',       label: 'Раствор глюкозы',           category: '🍞 Еда и вода' },
  // Detectors
  { value: 'echo_detector', label: 'Детектор «Эхо»',           category: '📡 Детекторы' },
  { value: 'bear_detector', label: 'Детектор «Медведь»',        category: '📡 Детекторы' },
  { value: 'veles_detector',label: 'Детектор «Велес»',          category: '📡 Детекторы' },
  // Secret documents
  { value: 'classified_report',    label: 'Секретный отчёт',             category: '📄 Секр. документы' },
  { value: 'encrypted_disk',       label: 'Зашифрованный диск',          category: '📄 Секр. документы' },
  { value: 'zone_research_notes',  label: 'Исследовательские записки',   category: '📄 Секр. документы' },
];

export function SpawnItemModal({
  onClose,
  onSave,
}: {
  onClose: () => void;
  onSave: (itemType: string) => Promise<void>;
}) {
  const [itemType, setItemType] = useState<string>(ITEM_TYPE_OPTIONS[0].value);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const handleSubmit = async () => {
    setSaving(true); setErr(null);
    try {
      await onSave(itemType);
    } catch (e: unknown) {
      setErr((e as { message?: string })?.message ?? 'Spawn failed');
      setSaving(false);
    }
  };

  // Group options by category for <optgroup>
  const categories = Array.from(new Set(ITEM_TYPE_OPTIONS.map((o) => o.category)));

  return (
    <div style={s.modalOverlay} onMouseDown={onClose}>
      <div style={s.modal} onMouseDown={(e) => e.stopPropagation()}>
        <h3 style={{ margin: '0 0 12px', color: '#f8fafc', fontSize: '1rem' }}>📦 Добавить предмет на локацию</h3>
        <label style={s.modalLabel}>Тип предмета</label>
        <select
          style={s.modalInput}
          value={itemType}
          onChange={(e) => setItemType(e.target.value)}
          autoFocus
        >
          {categories.map((cat) => (
            <optgroup key={cat} label={cat}>
              {ITEM_TYPE_OPTIONS.filter((o) => o.category === cat).map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </optgroup>
          ))}
        </select>
        {err && <div style={{ color: '#ef4444', fontSize: '0.72rem', marginTop: 6 }}>{err}</div>}
        <div style={{ display: 'flex', gap: 8, marginTop: 14, justifyContent: 'flex-end' }}>
          <button style={s.modalCancelBtn} onClick={onClose} disabled={saving}>Cancel</button>
          <button style={s.modalSaveBtn} onClick={handleSubmit} disabled={saving}>
            {saving ? 'Adding…' : 'Добавить'}
          </button>
        </div>
      </div>
    </div>
  );
}

export function SpawnArtifactModal({
  onClose,
  onSave,
}: {
  onClose: () => void;
  onSave: (artifactType: string) => Promise<void>;
}) {
  const [artifactType, setArtifactType] = useState<string>(ARTIFACT_TYPE_OPTIONS[0]);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const handleSubmit = async () => {
    setSaving(true); setErr(null);
    try {
      await onSave(artifactType);
    } catch (e: unknown) {
      setErr((e as { message?: string })?.message ?? 'Spawn failed');
      setSaving(false);
    }
  };

  return (
    <div style={s.modalOverlay} onMouseDown={onClose}>
      <div style={s.modal} onMouseDown={(e) => e.stopPropagation()}>
        <h3 style={{ margin: '0 0 12px', color: '#f8fafc', fontSize: '1rem' }}>💎 Spawn Artifact</h3>
        <label style={s.modalLabel}>Тип артефакта</label>
        <select
          style={s.modalInput}
          value={artifactType}
          onChange={(e) => setArtifactType(e.target.value)}
          autoFocus
        >
          {ARTIFACT_TYPE_OPTIONS.map((t) => (
            <option key={t} value={t}>{ARTIFACT_TYPE_LABELS[t] ?? t}</option>
          ))}
        </select>
        {err && <div style={{ color: '#ef4444', fontSize: '0.72rem', marginTop: 6 }}>{err}</div>}
        <div style={{ display: 'flex', gap: 8, marginTop: 14, justifyContent: 'flex-end' }}>
          <button style={s.modalCancelBtn} onClick={onClose} disabled={saving}>Cancel</button>
          <button style={s.modalSaveBtn} onClick={handleSubmit} disabled={saving}>
            {saving ? 'Spawning…' : 'Spawn'}
          </button>
        </div>
      </div>
    </div>
  );
}
