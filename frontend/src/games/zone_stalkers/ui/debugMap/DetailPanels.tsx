/**
 * DetailPanels — the right-hand side panels shown when a location or region
 * is selected, plus the empty hint when nothing is selected.
 */
import { useState } from 'react';
import { AgentCreateModal } from '../AgentProfileModal';
import type { ZoneLocation, ZoneMapState, LocationConn } from './types';
import { TERRAIN_TYPE_LABELS, REGION_COLOR_PALETTE } from './constants';
import { s } from './styles';
import { Badge, Section, DetailRow, EmptyRow } from './UIKit';
import { SpawnMutantModal, SpawnArtifactModal } from './Modals';

// ─── LocationDetailPanel ──────────────────────────────────────────────────────

export function LocationDetailPanel({
  loc,
  conns,
  regionName,
  zoneState,
  onClose,
  onEdit,
  onSpawnStalker,
  onSpawnTrader,
  onSpawnMutant,
  onSpawnArtifact,
  onDeleteConnection,
  onUpdateConnectionWeight,
  onToggleConnectionClosed,
  onAgentClick,
}: {
  loc: ZoneLocation;
  conns: LocationConn[];
  /**
   * Pre-resolved display name of the location's region (if any).
   * Pass `localRegions[loc.region]?.name ?? fallback` from the parent so this
   * panel never needs to import the region lookup maps directly.
   */
  regionName?: string;
  zoneState: ZoneMapState;
  onClose: () => void;
  onEdit: () => void;
  onSpawnStalker: (name: string, faction: string, globalGoal: string) => Promise<void>;
  onSpawnTrader: (name: string) => Promise<void>;
  onSpawnMutant: (mutantType: string) => Promise<void>;
  onSpawnArtifact: (artifactType: string) => Promise<void>;
  onDeleteConnection: (toId: string) => void;
  onUpdateConnectionWeight: (toId: string, travelTime: number) => void;
  onToggleConnectionClosed: (toId: string) => void;
  /** Called when the user clicks a stalker row; opens their profile. */
  onAgentClick?: (agentId: string) => void;
}) {
  const [showSpawnModal, setShowSpawnModal] = useState<'stalker' | 'trader' | 'mutant' | 'artifact' | null>(null);

  const stalkers = loc.agents.map((id) => zoneState.agents[id]).filter(Boolean);
  const mutants = loc.agents.map((id) => zoneState.mutants[id]).filter(Boolean);
  const aliveMutants = mutants.filter((m) => m.is_alive);
  const deadMutants = mutants.filter((m) => !m.is_alive);
  const traders = Object.values(zoneState.traders).filter((t) => t.location_id === loc.id);

  return (
    <div style={s.detail}>
      {/* Header */}
      <div style={s.detailHeader}>
        <div>
          <div style={s.detailName}>{loc.name}</div>
          <div style={s.detailMeta}>
            {TERRAIN_TYPE_LABELS[loc.terrain_type ?? ''] ?? (loc.terrain_type ?? '—')}
            {(loc.anomaly_activity ?? 0) > 0 && (
              <span style={{ color: '#a855f7', marginLeft: 6 }}>· ☢ {loc.anomaly_activity}</span>
            )}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
          <button onClick={onEdit} style={s.editDetailBtn} title="Редактировать локацию">
            ✏ Редактировать
          </button>
          <button onClick={onClose} style={s.closeBtn}>✕</button>
        </div>
      </div>

      {/* Characteristics */}
      <Section label="🌍 Характеристики">
        {regionName && (
          <DetailRow>
            <span style={{ color: '#94a3b8', fontSize: '0.72rem', width: 110, flexShrink: 0 }}>Регион</span>
            <span style={{ color: '#cbd5e1', fontSize: '0.8rem' }}>{regionName}</span>
          </DetailRow>
        )}
        {loc.terrain_type && (
          <DetailRow>
            <span style={{ color: '#94a3b8', fontSize: '0.72rem', width: 110, flexShrink: 0 }}>Местность</span>
            <span style={{ color: '#cbd5e1', fontSize: '0.8rem' }}>
              {TERRAIN_TYPE_LABELS[loc.terrain_type] ?? loc.terrain_type}
            </span>
          </DetailRow>
        )}
        <DetailRow>
          <span style={{ color: '#94a3b8', fontSize: '0.72rem', width: 110, flexShrink: 0 }}>Аном. активность</span>
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 6 }}>
            <div style={{ flex: 1, height: 5, background: '#0f172a', borderRadius: 3, overflow: 'hidden' }}>
              <div style={{ height: '100%', width: `${((loc.anomaly_activity ?? 0) / 10) * 100}%`, background: '#a855f7', borderRadius: 3 }} />
            </div>
            <span style={{ color: '#a855f7', fontSize: '0.7rem', width: 24, textAlign: 'right', flexShrink: 0 }}>
              {loc.anomaly_activity ?? 0}
            </span>
          </div>
        </DetailRow>
        {loc.dominant_anomaly_type && (
          <DetailRow>
            <span style={{ color: '#94a3b8', fontSize: '0.72rem', width: 110, flexShrink: 0 }}>Тип аномалий</span>
            <span style={{ color: '#e879f9', fontSize: '0.8rem' }}>{loc.dominant_anomaly_type}</span>
          </DetailRow>
        )}
        <DetailRow>
          <span style={{ color: '#94a3b8', fontSize: '0.72rem', width: 110, flexShrink: 0 }}>Артефактов</span>
          <span style={{ color: '#a5b4fc', fontSize: '0.8rem' }}>{loc.artifacts.length}</span>
        </DetailRow>
      </Section>

      {/* Connections */}
      <Section label="🔗 Connections">
        {conns.length === 0 ? (
          <EmptyRow />
        ) : (
          conns.map((c) => {
            const target = zoneState.locations[c.to];
            return (
              <DetailRow key={c.to}>
                <span style={{ color: '#cbd5e1', fontSize: '0.8rem', flex: 1 }}>
                  {target?.name ?? c.to}
                </span>
                <span style={{ color: c.type === 'dangerous' ? '#ef4444' : '#475569', fontSize: '0.68rem', marginRight: 4 }}>
                  {c.type}
                </span>
                <input
                  type="number"
                  min={1}
                  max={999}
                  value={c.travel_time ?? 15}
                  title="Время перехода (минут)"
                  onChange={(e) => {
                    const v = parseInt(e.target.value, 10);
                    if (!isNaN(v) && v > 0) onUpdateConnectionWeight(c.to, v);
                  }}
                  style={{
                    width: 44,
                    background: '#0f172a',
                    border: '1px solid #1e3a5f',
                    borderRadius: 4,
                    color: '#94a3b8',
                    fontSize: '0.72rem',
                    padding: '1px 4px',
                    textAlign: 'center',
                    marginRight: 4,
                  }}
                />
                <span style={{ color: '#475569', fontSize: '0.65rem', marginRight: 4 }}>м</span>
                <button
                  style={{
                    background: 'none',
                    border: 'none',
                    cursor: 'pointer',
                    fontSize: '0.8rem',
                    padding: '0 4px',
                    color: c.closed ? '#ef4444' : '#475569',
                  }}
                  onClick={() => onToggleConnectionClosed(c.to)}
                  title={c.closed ? 'Открыть переход' : 'Закрыть переход'}
                >
                  {c.closed ? '🔒' : '🔓'}
                </button>
                <button
                  style={s.connDelBtn}
                  onClick={() => onDeleteConnection(c.to)}
                  title="Delete this connection"
                >
                  ✕
                </button>
              </DetailRow>
            );
          })
        )}
      </Section>

      {/* Stalkers */}
      <Section label={`🧍 Сталкеры (${stalkers.length})`}>
        {stalkers.length === 0 ? (
          <EmptyRow />
        ) : (
          stalkers.map((a) => (
            <DetailRow
              key={a.id}
              style={onAgentClick ? { cursor: 'pointer' } : undefined}
              onClick={onAgentClick ? () => onAgentClick(a.id) : undefined}
            >
              <span style={{ color: a.is_alive ? '#f8fafc' : '#475569', fontSize: '0.8rem', flex: 1 }}>
                {a.name}
                {!a.is_alive && (
                  <span style={{ color: '#ef4444', fontSize: '0.65rem', marginLeft: 4 }}>(мёртв)</span>
                )}
              </span>
              <span style={{ color: '#64748b', fontSize: '0.68rem' }}>{a.hp}/{a.max_hp} HP</span>
              <span style={{
                background: a.controller.kind === 'human' ? '#1d4ed8' : '#1e293b',
                color: a.controller.kind === 'human' ? '#bfdbfe' : '#475569',
                borderRadius: 4,
                padding: '0 0.3rem',
                fontSize: '0.62rem',
                flexShrink: 0,
              }}>
                {a.controller.kind === 'human' ? '👤' : '🤖'}
              </span>
            </DetailRow>
          ))
        )}
      </Section>

      {/* Mutants */}
      <Section label={`☣️ Мутанты (${mutants.length})`}>
        {mutants.length === 0 ? (
          <EmptyRow />
        ) : (
          <>
            <div style={{ display: 'flex', gap: 10, marginBottom: 4, flexWrap: 'wrap' }}>
              <span style={{ color: '#fca5a5', fontSize: '0.72rem' }}>Живых: {aliveMutants.length}</span>
              {deadMutants.length > 0 && (
                <span style={{ color: '#475569', fontSize: '0.72rem' }}>Мёртвых: {deadMutants.length}</span>
              )}
            </div>
            {mutants.map((m) => (
              <DetailRow key={m.id}>
                <span style={{ color: m.is_alive ? '#fca5a5' : '#475569', fontSize: '0.8rem', flex: 1 }}>
                  {m.name}
                </span>
                <span style={{ color: '#64748b', fontSize: '0.68rem' }}>{m.hp}/{m.max_hp} HP</span>
              </DetailRow>
            ))}
          </>
        )}
      </Section>

      {/* Traders */}
      {traders.length > 0 && (
        <Section label="🏪 Traders">
          {traders.map((t) => (
            <DetailRow key={t.id}>
              <span style={{ color: '#fbbf24', fontSize: '0.8rem' }}>{t.name}</span>
            </DetailRow>
          ))}
        </Section>
      )}

      {/* Artifacts */}
      {loc.artifacts.length > 0 && (
        <Section label="💎 Artifacts">
          {loc.artifacts.map((a) => (
            <DetailRow key={a.id}>
              <span style={{ color: '#a5b4fc', fontSize: '0.8rem' }}>{a.name}</span>
              <span style={{ color: '#64748b', fontSize: '0.68rem' }}>{a.value}&nbsp;RU</span>
            </DetailRow>
          ))}
        </Section>
      )}

      {/* Ground items */}
      {loc.items.length > 0 && (
        <Section label="📦 Ground items">
          {loc.items.map((item) => (
            <DetailRow key={item.id}>
              <span style={{ color: '#cbd5e1', fontSize: '0.8rem' }}>{item.name}</span>
              <span style={{ color: '#64748b', fontSize: '0.68rem' }}>{item.type}</span>
            </DetailRow>
          ))}
        </Section>
      )}

      {/* Spawn controls */}
      <Section label="⚡ Spawn">
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <button style={s.spawnBtn} onClick={() => setShowSpawnModal('stalker')}>
            👤 Сталкер
          </button>
          <button style={{ ...s.spawnBtn, color: '#f59e0b' }} onClick={() => setShowSpawnModal('trader')}>
            🏪 Торговец
          </button>
          <button style={s.spawnBtn} onClick={() => setShowSpawnModal('mutant')}>
            ☣️ Мутант
          </button>
          <button style={{ ...s.spawnBtn, color: '#a5b4fc' }} onClick={() => setShowSpawnModal('artifact')}>
            💎 Артефакт
          </button>
        </div>
      </Section>

      <div style={{ color: '#1e293b', fontSize: '0.62rem', marginTop: 6 }}>id: {loc.id}</div>

      {showSpawnModal === 'stalker' && (
        <AgentCreateModal
          onClose={() => setShowSpawnModal(null)}
          onSave={async (name, faction, globalGoal, isTrader) => {
            if (isTrader) {
              await onSpawnTrader(name);
            } else {
              await onSpawnStalker(name, faction, globalGoal);
            }
            setShowSpawnModal(null);
          }}
        />
      )}
      {showSpawnModal === 'trader' && (
        <AgentCreateModal
          onClose={() => setShowSpawnModal(null)}
          onSave={async (name, _faction, _globalGoal, _isTrader) => {
            await onSpawnTrader(name);
            setShowSpawnModal(null);
          }}
          defaultIsTrader
        />
      )}
      {showSpawnModal === 'mutant' && (
        <SpawnMutantModal
          onClose={() => setShowSpawnModal(null)}
          onSave={async (mutantType) => { await onSpawnMutant(mutantType); setShowSpawnModal(null); }}
        />
      )}
      {showSpawnModal === 'artifact' && (
        <SpawnArtifactModal
          onClose={() => setShowSpawnModal(null)}
          onSave={async (artifactType) => { await onSpawnArtifact(artifactType); setShowSpawnModal(null); }}
        />
      )}
    </div>
  );
}

// ─── RegionDetailPanel ────────────────────────────────────────────────────────

export function RegionDetailPanel({
  regionId,
  region,
  locations,
  onClose,
  onSave,
  onDelete,
}: {
  regionId: string;
  region: { name: string; colorIndex: number };
  /** Locations that belong to this region, pre-filtered by the parent. */
  locations: { id: string; name: string }[];
  onClose: () => void;
  onSave: (name: string, colorIndex: number) => void;
  onDelete: () => void;
}) {
  const [name, setName] = useState(region.name);
  const [colorIndex, setColorIndex] = useState(region.colorIndex);

  return (
    <div style={s.detail}>
      <div style={s.detailHeader}>
        <div style={s.detailName}>🗺 {region.name}</div>
        <button onClick={onClose} style={s.closeBtn}>✕</button>
      </div>
      <div style={{ color: '#475569', fontSize: '0.65rem', marginBottom: 8 }}>ID: {regionId}</div>

      {/* Location list */}
      <Section label={`📍 Локации (${locations.length})`}>
        {locations.length === 0 ? (
          <EmptyRow />
        ) : (
          locations.map((loc) => (
            <DetailRow key={loc.id}>
              <span style={{ color: '#cbd5e1', fontSize: '0.8rem' }}>{loc.name}</span>
              <span style={{ color: '#334155', fontSize: '0.65rem' }}>{loc.id}</span>
            </DetailRow>
          ))
        )}
      </Section>

      <Section label="⚙ Настройки региона">
        <label style={s.modalLabel}>Название</label>
        <input
          style={s.modalInput}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <label style={s.modalLabel}>Цвет</label>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
          {REGION_COLOR_PALETTE.map((c, i) => (
            <div
              key={i}
              onClick={() => setColorIndex(i)}
              style={{
                width: 24,
                height: 24,
                borderRadius: 6,
                background: c.bg,
                border: `3px solid ${colorIndex === i ? '#f8fafc' : c.border}`,
                cursor: 'pointer',
              }}
            />
          ))}
        </div>
        <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
          <button
            style={s.modalSaveBtn}
            onClick={() => onSave(name.trim() || region.name, colorIndex)}
          >
            💾 Сохранить
          </button>
          <button
            style={{ ...s.modalCancelBtn, color: '#ef4444', borderColor: '#7f1d1d' }}
            onClick={onDelete}
          >
            🗑 Удалить регион
          </button>
        </div>
      </Section>
    </div>
  );
}

// ─── EmptyDetailHint ──────────────────────────────────────────────────────────

export function EmptyDetailHint({ totalLocs }: { totalLocs: number }) {
  return (
    <div style={s.emptyDetail}>
      <div style={s.emptyDetailTitle}>Location Details</div>
      <p style={s.emptyDetailHint}>
        Кликните на карточку локации на карте, чтобы увидеть детали и кнопку редактирования.
      </p>
      <hr style={s.hr} />
      <div style={{ color: '#334155', fontSize: '0.7rem' }}>
        {totalLocs} locations on map
      </div>
    </div>
  );
}

// Re-export Badge so consumers only need one import for card rendering
export { Badge };
