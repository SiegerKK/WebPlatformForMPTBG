/**
 * DetailPanels — the right-hand side panels shown when a location or region
 * is selected, plus the empty hint when nothing is selected.
 */
import { useState, useRef } from 'react';
import { AgentCreateModal } from '../AgentProfileModal';
import type { ZoneLocation, ZoneMapState, LocationConn, LocationImageSlot } from './types';
import {
  LOCATION_IMAGE_SLOTS,
  LOCATION_IMAGE_SLOT_LABELS,
  LOCATION_IMAGE_SLOT_ICONS,
  getPrimaryLocationImageUrl,
} from './types';
import { TERRAIN_TYPE_LABELS, REGION_COLOR_PALETTE } from './constants';
import { s } from './styles';
import { Badge, Section, DetailRow, EmptyRow } from './UIKit';
import { SpawnMutantModal, SpawnArtifactModal, SpawnItemModal } from './Modals';

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
  onSpawnItem,
  onDeleteConnection,
  onUpdateConnectionWeight,
  onToggleConnectionClosed,
  onAgentClick,
  onTraderClick,
  onDeleteLoc,
  onUploadLocationImageSlot,
  onDeleteLocationImageSlot,
  onSetPrimaryImageSlot,
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
  onSpawnStalker: (name: string, faction: string, globalGoal: string, killTargetId?: string) => Promise<void>;
  onSpawnTrader: (name: string) => Promise<void>;
  onSpawnMutant: (mutantType: string) => Promise<void>;
  onSpawnArtifact: (artifactType: string) => Promise<void>;
  onSpawnItem: (itemType: string) => Promise<void>;
  onDeleteConnection: (toId: string) => void;
  onUpdateConnectionWeight: (toId: string, travelTime: number) => void;
  onToggleConnectionClosed: (toId: string) => void;
  /** Called when the user clicks a stalker row; opens their profile. */
  onAgentClick?: (agentId: string) => void;
  /** Called when the user clicks a trader row; opens their profile. */
  onTraderClick?: (traderId: string) => void;
  /** Called when the user wants to delete this location entirely. */
  onDeleteLoc?: () => void;
  /** Upload an image for a specific slot. */
  onUploadLocationImageSlot?: (slot: LocationImageSlot, file: File) => Promise<void>;
  /** Delete the image for a specific slot. */
  onDeleteLocationImageSlot?: (slot: LocationImageSlot) => Promise<void>;
  /** Set the primary image slot. */
  onSetPrimaryImageSlot?: (slot: LocationImageSlot) => Promise<void>;
}) {
  const [showSpawnModal, setShowSpawnModal] = useState<'stalker' | 'trader' | 'mutant' | 'artifact' | 'item' | null>(null);
  const [selectedSlot, setSelectedSlot] = useState<LocationImageSlot>('clear');
  const [imgUploading, setImgUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── Safe collection lookups ──────────────────────────────────────────────
  const agentsById = zoneState.agents ?? {};
  const tradersById = zoneState.traders ?? {};
  const mutantsById = zoneState.mutants ?? {};
  const occupantIds: string[] = Array.isArray(loc.agents) ? loc.agents : [];

  // Build a unified person list from occupantIds — IDs may belong to agents OR traders.
  type PersonEntry = {
    id: string;
    name: string;
    isTrader: boolean;
    hp: number;
    max_hp: number;
    is_alive: boolean;
    controller: { kind: string };
  };
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
        controller: stalker.controller ?? { kind: 'bot' },
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
        controller: { kind: 'npc' },
      }];
    }
    return [];
  });

  const mutants = occupantIds
    .map((id) => mutantsById[id])
    .filter((m): m is NonNullable<typeof m> => Boolean(m));
  const aliveMutants = mutants.filter((m) => m.is_alive);
  const deadMutants = mutants.filter((m) => !m.is_alive);

  // IDs that are in loc.agents but not found in any known collection
  const unknownOccupantIds = occupantIds.filter(
    (id) => !agentsById[id] && !tradersById[id] && !mutantsById[id],
  );

  const locationTrace = zoneState.debug?.location_hunt_traces?.[loc.id];
  const positiveLeads = locationTrace?.positive_leads ?? [];
  const negativeLeads = locationTrace?.negative_leads ?? [];
  const exhaustedFor = locationTrace?.is_exhausted_for ?? [];
  const routesIn = locationTrace?.routes_in ?? [];
  const routesOut = locationTrace?.routes_out ?? [];
  const combatHuntEvents = locationTrace?.combat_hunt_events ?? [];
  const getAgentName = (id?: string | null) => {
    if (!id) return "unknown";
    return agentsById[id]?.name ?? tradersById[id]?.name ?? id;
  };

  const primaryImageUrl = getPrimaryLocationImageUrl(loc);

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !onUploadLocationImageSlot) return;
    setImgUploading(true);
    try {
      await onUploadLocationImageSlot(selectedSlot, file);
    } finally {
      setImgUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  return (
    <div style={s.detail}>
      {/* Header */}
      <div style={s.detailHeader}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ ...s.detailName, wordBreak: 'break-word', overflowWrap: 'anywhere' }}>{loc.name}</div>
          <div style={s.detailMeta}>
            {TERRAIN_TYPE_LABELS[loc.terrain_type ?? ''] ?? (loc.terrain_type ?? '—')}
            {(loc.anomaly_activity ?? 0) > 0 && (
              <span style={{ color: '#a855f7', marginLeft: 6 }}>· ☢ {loc.anomaly_activity}</span>
            )}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'flex-start', flexShrink: 0 }}>
          <button onClick={onEdit} style={s.editDetailBtn} title="Редактировать локацию">
            ✏ Редактировать
          </button>
          {onDeleteLoc && (
            <button
              onClick={onDeleteLoc}
              style={{ ...s.editDetailBtn, color: '#ef4444', borderColor: '#7f1d1d' }}
              title="Удалить локацию"
            >
              🗑 Удалить
            </button>
          )}
          <button onClick={onClose} style={s.closeBtn}>✕</button>
        </div>
      </div>

      {/* Image Slots */}
      <Section label="🖼 Изображения локации">
        {/* Primary image preview */}
        {primaryImageUrl ? (
          <img
            key={primaryImageUrl}
            src={primaryImageUrl}
            alt={loc.name}
            style={{
              width: '100%',
              maxHeight: 360,
              minHeight: 220,
              borderRadius: 8,
              objectFit: 'cover',
              border: '1px solid #1e3a5f',
              background: '#020617',
              display: 'block',
            }}
          />
        ) : (
          <div style={{
            width: '100%',
            minHeight: 80,
            borderRadius: 8,
            border: '1px dashed #1e3a5f',
            background: '#020617',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#334155',
            fontSize: '0.75rem',
          }}>
            Нет изображения
          </div>
        )}

        {/* Slot selector buttons (show primary / has-image indicators) */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 6 }}>
          {LOCATION_IMAGE_SLOTS.map((slot) => {
            const hasImage = Boolean(loc.image_slots?.[slot]);
            const isPrimary = loc.primary_image_slot === slot || (!loc.primary_image_slot && slot === 'clear' && Boolean(loc.image_url) && !loc.image_slots);
            return (
              <button
                key={slot}
                onClick={() => {
                  if (hasImage && onSetPrimaryImageSlot) onSetPrimaryImageSlot(slot);
                }}
                disabled={!hasImage || !onSetPrimaryImageSlot}
                title={hasImage ? 'Сделать приоритетной' : 'Сначала загрузите изображение'}
                style={{
                  padding: '2px 7px',
                  fontSize: '0.68rem',
                  cursor: hasImage && onSetPrimaryImageSlot ? 'pointer' : 'default',
                  background: isPrimary ? '#1d4ed8' : (hasImage ? '#1e293b' : '#0f172a'),
                  color: isPrimary ? '#bfdbfe' : (hasImage ? '#94a3b8' : '#334155'),
                  borderTop: `1px solid ${isPrimary ? '#3b82f6' : (hasImage ? '#334155' : '#1e293b')}`,
                  borderRight: `1px solid ${isPrimary ? '#3b82f6' : (hasImage ? '#334155' : '#1e293b')}`,
                  borderBottom: `1px solid ${isPrimary ? '#3b82f6' : (hasImage ? '#334155' : '#1e293b')}`,
                  borderLeft: `3px solid ${isPrimary ? '#60a5fa' : (hasImage ? '#334155' : '#1e293b')}`,
                  borderRadius: 4,
                  transition: 'background 0.15s',
                }}
              >
                {LOCATION_IMAGE_SLOT_ICONS[slot]} {isPrimary ? '★ ' : ''}{LOCATION_IMAGE_SLOT_LABELS[slot]}
              </button>
            );
          })}
        </div>

        {/* Upload / delete controls */}
        {(onUploadLocationImageSlot || onDeleteLocationImageSlot) && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center', marginTop: 6 }}>
            <select
              value={selectedSlot}
              onChange={(e) => setSelectedSlot(e.target.value as LocationImageSlot)}
              style={{
                background: '#0f172a',
                color: '#94a3b8',
                border: '1px solid #334155',
                borderRadius: 4,
                fontSize: '0.72rem',
                padding: '2px 4px',
                cursor: 'pointer',
              }}
            >
              {LOCATION_IMAGE_SLOTS.map((slot) => (
                <option key={slot} value={slot}>
                  {LOCATION_IMAGE_SLOT_ICONS[slot]} {LOCATION_IMAGE_SLOT_LABELS[slot]}
                </option>
              ))}
            </select>

            {onUploadLocationImageSlot && (
              <label
                style={{
                  ...s.spawnBtn,
                  color: '#86efac',
                  cursor: imgUploading ? 'wait' : 'pointer',
                  opacity: imgUploading ? 0.6 : 1,
                  display: 'inline-block',
                }}
                title={`Загрузить изображение для слота «${LOCATION_IMAGE_SLOT_LABELS[selectedSlot]}»`}
              >
                {imgUploading ? '⏳' : '📤'} Загрузить
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/jpeg,image/png,image/webp,image/gif"
                  style={{ display: 'none' }}
                  onChange={handleFileChange}
                  disabled={imgUploading}
                />
              </label>
            )}

            {onDeleteLocationImageSlot && loc.image_slots?.[selectedSlot] && (
              <button
                style={{ ...s.spawnBtn, color: '#ef4444', borderColor: '#7f1d1d' }}
                onClick={() => onDeleteLocationImageSlot(selectedSlot)}
                title={`Удалить изображение слота «${LOCATION_IMAGE_SLOT_LABELS[selectedSlot]}»`}
              >
                🗑 Удалить
              </button>
            )}
          </div>
        )}
      </Section>

      {/* Characteristics */}
      <Section label="🌍 Характеристики">
        {regionName && (
          <DetailRow>
            <span style={{ color: '#94a3b8', fontSize: '0.72rem', width: 110, flexShrink: 0 }}>Регион</span>
            <span style={{ color: '#cbd5e1', fontSize: '0.8rem', minWidth: 0, wordBreak: 'break-word' }}>{regionName}</span>
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
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
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
                <span style={{ color: '#cbd5e1', fontSize: '0.8rem', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {target?.name ?? c.to}
                </span>
                <span style={{ color: c.type === 'dangerous' ? '#ef4444' : '#475569', fontSize: '0.68rem', marginRight: 4, flexShrink: 0 }}>
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
                    flexShrink: 0,
                  }}
                />
                <span style={{ color: '#475569', fontSize: '0.65rem', marginRight: 4, flexShrink: 0 }}>м</span>
                <button
                  style={{
                    background: 'none',
                    border: 'none',
                    cursor: 'pointer',
                    fontSize: '0.8rem',
                    padding: '0 4px',
                    color: c.closed ? '#ef4444' : '#475569',
                    flexShrink: 0,
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

      <Section label="🕵️ Следы / Hunt Traces">
        {(positiveLeads.length === 0 && negativeLeads.length === 0 && exhaustedFor.length === 0 && routesIn.length === 0 && routesOut.length === 0 && combatHuntEvents.length === 0) ? (
          <EmptyRow />
        ) : (
          <>
            <DetailRow>
              <span style={{ color: '#94a3b8', fontSize: '0.72rem', width: 120, flexShrink: 0 }}>Положительные</span>
              <span style={{ color: '#67e8f9', fontSize: '0.78rem' }}>{positiveLeads.length}</span>
            </DetailRow>
            <DetailRow>
              <span style={{ color: '#94a3b8', fontSize: '0.72rem', width: 120, flexShrink: 0 }}>Негативные</span>
              <span style={{ color: '#fda4af', fontSize: '0.78rem' }}>{negativeLeads.length}</span>
            </DetailRow>
            <DetailRow>
              <span style={{ color: '#94a3b8', fontSize: '0.72rem', width: 120, flexShrink: 0 }}>Exhausted для</span>
              <span style={{ color: '#fda4af', fontSize: '0.78rem' }}>{exhaustedFor.length}</span>
            </DetailRow>
            <DetailRow>
              <span style={{ color: '#94a3b8', fontSize: '0.72rem', width: 120, flexShrink: 0 }}>Routes in / out</span>
              <span style={{ color: '#a5b4fc', fontSize: '0.78rem' }}>{routesIn.length} / {routesOut.length}</span>
            </DetailRow>
            <DetailRow>
              <span style={{ color: '#94a3b8', fontSize: '0.72rem', width: 120, flexShrink: 0 }}>Events</span>
              <span style={{ color: '#fbbf24', fontSize: '0.78rem' }}>{combatHuntEvents.length}</span>
            </DetailRow>

            {positiveLeads.slice(0, 8).map((lead) => (
              <DetailRow key={`lead-${lead.id}`}>
                <span style={{ color: '#cbd5e1', fontSize: '0.75rem', flex: 1, minWidth: 0 }}>
                  {lead.kind} · {Math.round(lead.confidence * 100)}% · t{lead.turn}
                </span>
                <span style={{ color: '#64748b', fontSize: '0.68rem', flexShrink: 0 }}>
                  {getAgentName(lead.hunter_id)} → {lead.target_id ?? "target?"}
                </span>
              </DetailRow>
            ))}

            {negativeLeads.slice(0, 10).map((lead) => (
              <DetailRow key={`neg-${lead.id}`}>
                <span style={{ color: '#fda4af', fontSize: '0.75rem', flex: 1, minWidth: 0 }}>
                  {lead.kind}
                  {lead.source_kind ? ` · src=${lead.source_kind}` : ""}
                  {lead.failed_search_count != null && lead.failed_search_count > 0 ? ` · miss=${lead.failed_search_count}` : ""}
                  {lead.cooldown_until_turn ? ` · cd→${lead.cooldown_until_turn}` : ""}
                  {" · "}
                  {Math.round(lead.confidence * 100)}% · t{lead.turn}
                </span>
                <span style={{ color: '#64748b', fontSize: '0.68rem', flexShrink: 0 }}>
                  {lead.source_ref}
                </span>
              </DetailRow>
            ))}

            {exhaustedFor.slice(0, 8).map((item) => (
              <DetailRow key={`exh-${item.hunter_id}-${item.target_id ?? "target"}`}>
                <span style={{ color: '#fda4af', fontSize: '0.75rem', flex: 1, minWidth: 0 }}>
                  ⛔ exhausted
                  {item.failed_search_count ? ` · miss=${item.failed_search_count}` : ""}
                  {item.cooldown_until_turn ? ` · cd→${item.cooldown_until_turn}` : ""}
                </span>
                <span style={{ color: '#64748b', fontSize: '0.68rem', flexShrink: 0 }}>
                  {getAgentName(item.hunter_id)} → {item.target_id ?? "target?"}
                </span>
              </DetailRow>
            ))}

            {routesIn.slice(0, 10).map((route) => (
              <DetailRow key={`in-${route.source_ref}`}>
                <span style={{ color: '#a5b4fc', fontSize: '0.75rem', flex: 1, minWidth: 0 }}>
                  ← {route.from_location_id ?? 'unknown'} · {Math.round(route.confidence * 100)}% · {route.reason} · t{route.turn}
                </span>
                <span style={{ color: '#64748b', fontSize: '0.68rem', flexShrink: 0 }}>{getAgentName(route.hunter_id)}</span>
              </DetailRow>
            ))}

            {routesOut.slice(0, 10).map((route) => (
              <DetailRow key={`out-${route.source_ref}`}>
                <span style={{ color: '#a5b4fc', fontSize: '0.75rem', flex: 1, minWidth: 0 }}>
                  → {route.to_location_id ?? 'unknown'} · {Math.round(route.confidence * 100)}% · {route.reason} · t{route.turn}
                </span>
                <span style={{ color: '#64748b', fontSize: '0.68rem', flexShrink: 0 }}>{getAgentName(route.hunter_id)}</span>
              </DetailRow>
            ))}

            {combatHuntEvents.slice(0, 10).map((event) => (
              <DetailRow key={`evt-${event.source_ref}`}>
                <span style={{ color: '#fbbf24', fontSize: '0.75rem', flex: 1, minWidth: 0 }}>
                  {event.kind} · t{event.turn} · {event.summary}
                </span>
                <span style={{ color: '#64748b', fontSize: '0.68rem', flexShrink: 0 }}>{getAgentName(event.hunter_id)}</span>
              </DetailRow>
            ))}
          </>
        )}
      </Section>

      {/* Stalkers + Traders (unified) */}
      <Section label={`🧍 Персонажи (${allPersons.length})`}>
        {allPersons.length === 0 ? (
          <EmptyRow />
        ) : (
          allPersons.map((a) => {
            const isClickable = a.isTrader ? !!onTraderClick : !!onAgentClick;
            const handleClick = a.isTrader
              ? (onTraderClick ? () => onTraderClick(a.id) : undefined)
              : (onAgentClick ? () => onAgentClick(a.id) : undefined);
            return (
              <DetailRow
                key={a.id}
                style={isClickable ? { cursor: 'pointer' } : undefined}
                onClick={handleClick}
              >
                <span style={{ color: a.is_alive ? '#f8fafc' : '#475569', fontSize: '0.8rem', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {a.name}
                  {!a.is_alive && (
                    <span style={{ color: '#ef4444', fontSize: '0.65rem', marginLeft: 4 }}>(мёртв)</span>
                  )}
                  {a.isTrader && (
                    <span style={{ fontSize: '0.75rem', marginLeft: 5 }} title="Торговец">🏪</span>
                  )}
                </span>
                {!a.isTrader && (
                  <span style={{ color: '#64748b', fontSize: '0.68rem', flexShrink: 0 }}>{a.hp}/{a.max_hp} HP</span>
                )}
                <span style={{
                  background: a.isTrader ? '#78350f' : (a.controller.kind === 'human' ? '#1d4ed8' : '#1e293b'),
                  color: a.isTrader ? '#fde68a' : (a.controller.kind === 'human' ? '#bfdbfe' : '#475569'),
                  borderRadius: 4,
                  padding: '0 0.3rem',
                  fontSize: '0.62rem',
                  flexShrink: 0,
                }}>
                  {a.isTrader ? '🏪' : (a.controller.kind === 'human' ? '👤' : '🤖')}
                </span>
              </DetailRow>
            );
          })
        )}
      </Section>

      {/* Unknown occupants — debug only */}
      {unknownOccupantIds.length > 0 && (
        <Section label={`⚠️ Unknown occupants (${unknownOccupantIds.length})`}>
          {unknownOccupantIds.map((id) => (
            <DetailRow key={id}>
              <span style={{ color: '#fca5a5', fontSize: '0.75rem', wordBreak: 'break-all', flex: 1 }}>
                {id}
              </span>
            </DetailRow>
          ))}
        </Section>
      )}

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
                <span style={{ color: m.is_alive ? '#fca5a5' : '#475569', fontSize: '0.8rem', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {m.name}
                </span>
                <span style={{ color: '#64748b', fontSize: '0.68rem', flexShrink: 0 }}>{m.hp}/{m.max_hp} HP</span>
              </DetailRow>
            ))}
          </>
        )}
      </Section>

      {/* Artifacts */}
      {loc.artifacts.length > 0 && (
        <Section label="💎 Artifacts">
          {loc.artifacts.map((a) => (
            <DetailRow key={a.id}>
              <span style={{ color: '#a5b4fc', fontSize: '0.8rem', flex: 1, minWidth: 0 }}>{a.name}</span>
              <span style={{ color: '#64748b', fontSize: '0.68rem', flexShrink: 0 }}>{a.value}&nbsp;RU</span>
            </DetailRow>
          ))}
        </Section>
      )}

      {/* Ground items */}
      {loc.items.length > 0 && (
        <Section label="📦 Ground items">
          {loc.items.map((item) => (
            <DetailRow key={item.id}>
              <span style={{ color: '#cbd5e1', fontSize: '0.8rem', flex: 1, minWidth: 0 }}>{item.name}</span>
              <span style={{ color: '#64748b', fontSize: '0.68rem', flexShrink: 0 }}>{item.type}</span>
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
          <button style={{ ...s.spawnBtn, color: '#86efac' }} onClick={() => setShowSpawnModal('item')}>
            📦 Предмет
          </button>
        </div>
      </Section>

      <div style={{ color: '#1e293b', fontSize: '0.62rem', marginTop: 6 }}>id: {loc.id}</div>

      {showSpawnModal === 'stalker' && (
        <AgentCreateModal
          onClose={() => setShowSpawnModal(null)}
          onSave={async (name, faction, globalGoal, isTrader, killTargetId) => {
            if (isTrader) {
              await onSpawnTrader(name);
            } else {
              await onSpawnStalker(name, faction, globalGoal, killTargetId);
            }
            setShowSpawnModal(null);
          }}
          agents={zoneState.agents}
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
      {showSpawnModal === 'item' && (
        <SpawnItemModal
          onClose={() => setShowSpawnModal(null)}
          onSave={async (itemType) => { await onSpawnItem(itemType); setShowSpawnModal(null); }}
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
        <div style={{ ...s.detailName, wordBreak: 'break-word', overflowWrap: 'anywhere', minWidth: 0, flex: 1 }}>🗺 {region.name}</div>
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
              <span style={{ color: '#cbd5e1', fontSize: '0.8rem', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{loc.name}</span>
              <span style={{ color: '#334155', fontSize: '0.65rem', flexShrink: 0 }}>{loc.id}</span>
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

// Re-export Badge for backward compat with DebugMapPage import
export { Badge };
