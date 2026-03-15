/**
 * Modals — LocationModal (edit/create location) and SpawnMutantModal.
 */
import { useState } from 'react';
import { s } from './styles';

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
}

export function LocationModal({
  mode,
  initialName = '',
  initialTerrainType = 'plain',
  initialAnomalyActivity = 0,
  initialDominantAnomalyType = '',
  initialRegion = '',
  regions,
  locId,
  onClose,
  onSave,
}: {
  mode: 'edit' | 'create';
  initialName?: string;
  initialTerrainType?: string;
  initialAnomalyActivity?: number;
  initialDominantAnomalyType?: string;
  initialRegion?: string;
  regions?: Record<string, { name: string; colorIndex: number }>;
  locId?: string;
  onClose: () => void;
  onSave: (data: LocationSaveData) => Promise<void>;
}) {
  const [name, setName] = useState(initialName);
  const [terrainType, setTerrainType] = useState(initialTerrainType);
  const [anomalyActivity, setAnomalyActivity] = useState(initialAnomalyActivity);
  const [dominantAnomalyType, setDominantAnomalyType] = useState(initialDominantAnomalyType);
  const [region, setRegion] = useState(initialRegion);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const handleSubmit = async () => {
    const trimmed = name.trim();
    if (!trimmed) { setErr('Name cannot be empty'); return; }
    setSaving(true); setErr(null);
    try {
      await onSave({ name: trimmed, terrainType, anomalyActivity, dominantAnomalyType, region });
      onClose();
    } catch (e: unknown) {
      setErr((e as { message?: string })?.message ?? 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={s.modalOverlay} onMouseDown={onClose}>
      <div style={s.modal} onMouseDown={(e) => e.stopPropagation()}>
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
