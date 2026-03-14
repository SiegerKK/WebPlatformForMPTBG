import React, { useCallback, useEffect, useRef, useState } from 'react';
import { commandsApi, contextsApi, eventsApi, matchesApi } from '../../../api/client';
import type { GameContext, GameEvent, Match, MatchParticipant, User } from '../../../types';
import DebugMapPage from './DebugMapPage';
import AgentRow from './AgentRow';
import type { AgentForProfile } from './AgentProfileModal';

// ─── DebugTimeControl ────────────────────────────────────────────────────────
function DebugTimeControl({
  worldDay, worldHour, worldMinute, onSetTime, disabled,
}: {
  worldDay: number; worldHour: number; worldMinute: number;
  onSetTime: (day: number, hour: number, minute: number) => void;
  disabled?: boolean;
}) {
  const [day, setDay] = React.useState(String(worldDay));
  const [hour, setHour] = React.useState(String(worldHour));
  const [minute, setMinute] = React.useState(String(worldMinute));

  React.useEffect(() => {
    setDay(String(worldDay));
    setHour(String(worldHour));
    setMinute(String(worldMinute));
  }, [worldDay, worldHour, worldMinute]);

  const inputStyle: React.CSSProperties = {
    width: 52, padding: '0.3rem 0.4rem', background: '#1e293b', color: '#f8fafc',
    border: '1px solid #334155', borderRadius: 6, fontSize: '0.85rem', textAlign: 'center',
  };
  const labelStyle: React.CSSProperties = { color: '#94a3b8', fontSize: '0.78rem', marginRight: 4 };

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
      <label style={labelStyle}>День</label>
      <input type="number" min="1" style={inputStyle} value={day}
        onChange={e => setDay(e.target.value)} />
      <label style={labelStyle}>Час (0–23)</label>
      <input type="number" min="0" max="23" style={inputStyle} value={hour}
        onChange={e => setHour(e.target.value)} />
      <label style={labelStyle}>Мин (0–59)</label>
      <input type="number" min="0" max="59" style={inputStyle} value={minute}
        onChange={e => setMinute(e.target.value)} />
      <button
        style={{ padding: '0.3rem 0.8rem', background: '#1e40af', color: '#bfdbfe', border: '1px solid #3b82f6', borderRadius: 6, cursor: 'pointer', fontSize: '0.85rem', fontWeight: 600 }}
        disabled={disabled}
        onClick={() => onSetTime(parseInt(day) || 1, parseInt(hour) || 0, parseInt(minute) || 0)}
      >
        ⏱ Установить время
      </button>
    </div>
  );
}

interface Props {
  match: Match;
  user: User;
  onMatchUpdated: (match: Match) => void;
  onMatchDeleted: (matchId: string) => void;
}

// ─── State types pulled from state_blob ─────────────────────────────────────

interface LocationConn {
  to: string;
  type: string;
}

interface ZoneLocation {
  id: string;
  name: string;
  terrain_type?: string;
  anomaly_activity?: number;
  dominant_anomaly_type?: string | null;
  connections: LocationConn[];
  anomalies: Array<{ id: string; type: string; name: string }>;
  artifacts: Array<{ id: string; type: string; name: string; value: number }>;
  items: Array<{ id: string; type: string; name: string }>;
  agents: string[];
}

interface AgentInventoryItem {
  id: string;
  type: string;
  name: string;
  weight?: number;
  value?: number;
}

interface ScheduledAction {
  type: 'travel' | 'explore' | 'sleep' | 'event';
  turns_remaining: number;
  turns_total: number;
  target_id: string;
  route?: string[];
  started_turn: number;
}

interface MemoryEntry {
  world_turn: number;
  world_day: number;
  world_hour: number;
  world_minute: number;
  type: string;
  title: string;
  summary: string;
  effects: Record<string, number>;
}

interface StalkerAgent {
  id: string;
  name: string;
  location_id: string;
  hp: number;
  max_hp: number;
  radiation: number;
  hunger: number;
  thirst: number;
  sleepiness: number;
  money: number;
  faction: string;
  inventory: AgentInventoryItem[];
  equipment: Record<string, AgentInventoryItem | null>;
  is_alive: boolean;
  action_used: boolean;
  scheduled_action: ScheduledAction | null;
  memory: MemoryEntry[];
  controller: { kind: string; participant_id?: string | null };
  // development / psychology (may be absent on older saves)
  experience?: number;
  skill_combat?: number;
  skill_stalker?: number;
  skill_trade?: number;
  skill_medicine?: number;
  skill_social?: number;
  global_goal?: string;
  current_goal?: string | null;
  risk_tolerance?: number;
  reputation?: number;
}

interface ZoneMapState {
  context_type: string;
  world_turn: number;
  world_hour: number;
  world_minute: number;
  world_day: number;
  max_turns: number;
  locations: Record<string, ZoneLocation>;
  agents: Record<string, StalkerAgent>;
  mutants: Record<string, { id: string; name: string; location_id: string; hp: number; max_hp: number; is_alive: boolean }>;
  traders: Record<string, { id: string; name: string; location_id: string }>;
  player_agents: Record<string, string>;
  active_events: string[];
  game_over: boolean;
}

interface ZoneEventState {
  context_type: string;
  event_id: string;
  title: string;
  description: string;
  phase: 'waiting' | 'active' | 'ended';
  current_turn: number;
  max_turns: number;
  participants: Record<string, { player_id: string; status: string; choice: number | null }>;
  current_narration: string;
  current_options: string[];
  narration_history: Array<{
    turn: number;
    narration: string;
    options: string[];
    choices: Record<string, string>;
  }>;
  outcome: string | null;
}

// ─── Location type colour ────────────────────────────────────────────────────

const TERRAIN_TYPE_COLOR: Record<string, string> = {
  plain: '#166534',
  hills: '#4c1d95',
  slag_heaps: '#334155',
  industrial: '#854d0e',
  buildings: '#3730a3',
  military_buildings: '#991b1b',
  hamlet: '#9a3412',
  farm: '#14532d',
  field_camp: '#134e4a',
};

const TERRAIN_TYPE_LABELS: Record<string, string> = {
  plain: 'Равнина',
  hills: 'Холмы',
  slag_heaps: 'Террикони',
  industrial: 'Промзона',
  buildings: 'Здания',
  military_buildings: 'Воен. здания',
  hamlet: 'Хутор',
  farm: 'Ферма',
  field_camp: 'Пол. лагерь',
};

const TIME_LABEL = (h: number, m: number) => `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;

const SCHED_ICONS: Record<string, string> = {
  travel: '🚶',
  explore: '🔍',
  sleep: '😴',
  event: '📖',
};

export default function ZoneStalkerGame({ match, user, onMatchUpdated, onMatchDeleted }: Props) {
  const [context, setContext] = useState<GameContext | null>(null);
  const [events, setEvents] = useState<GameEvent[]>([]);
  const [participants, setParticipants] = useState<MatchParticipant[]>([]);
  const [activeEventCtx, setActiveEventCtx] = useState<GameContext | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [, setTakingControlOf] = useState<string | null>(null);
  const [selectedLocId, setSelectedLocId] = useState<string | null>(null);
  const [travelTarget, setTravelTarget] = useState<string | null>(null);
  const [sleepHours, setSleepHours] = useState(6);
  const [showTravelPanel, setShowTravelPanel] = useState(false);
  const [activeTab, setActiveTab] = useState<'map' | 'event' | 'memory'>('map');
  // ── Entry / Roster state ─────────────────────────────────────────────────
  // The main menu is ALWAYS the entry screen — no sessionStorage skip.
  const [showEntryMenu, setShowEntryMenu] = useState<boolean>(true);
  // Which sub-screen of the entry flow is active
  const [entryScreen, setEntryScreen] = useState<'main' | 'npc_select'>('main');
  // In-game roster overlay (toggled by the 👥 Roster button while already playing)
  const [showRoster, setShowRoster] = useState(false);
  // When true, the full-screen Debug panel is shown (with its own sub-tabs)
  const [showDebug, setShowDebug] = useState(false);
  // Sub-tab within the debug panel
  const [debugTab, setDebugTab] = useState<'map' | 'characters' | 'global'>('map');

  // Enter game as your own assigned character
  const enterGame = () => {
    setShowEntryMenu(false);
    setShowDebug(false);
  };

  // Enter the debug panel
  const enterAsDebug = () => {
    setShowEntryMenu(false);
    setShowDebug(true);
    setDebugTab('map');
  };

  // Agent whose profile modal is open (null = list view)
  const [profileAgentId, setProfileAgentId] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const lobbyPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const zoneState: ZoneMapState | null = context
    ? (context.state_blob as unknown as ZoneMapState)
    : null;

  const myAgentId = zoneState?.player_agents?.[user.id] ?? null;
  const myAgent: StalkerAgent | null = myAgentId ? (zoneState?.agents?.[myAgentId] ?? null) : null;
  const currentLocId = myAgent?.location_id ?? null;
  const currentLoc = currentLocId ? zoneState?.locations?.[currentLocId] : null;

  const isCreator = match.created_by_user_id === user.id;
  const isWaiting = match.status === 'waiting_for_players' || match.status === 'draft';
  const isActive = match.status === 'active';

  const canAct = myAgent?.is_alive &&
    !myAgent?.action_used &&
    !myAgent?.scheduled_action &&
    !zoneState?.game_over;

  const eventState: ZoneEventState | null =
    activeEventCtx ? (activeEventCtx.state_blob as unknown as ZoneEventState) : null;
  const myEventParticipation = eventState?.participants?.[user.id];
  const canChooseOption =
    eventState?.phase === 'active' &&
    myEventParticipation?.status === 'active' &&
    myEventParticipation?.choice == null;

  // ─── load participants ───────────────────────────────────────────────────
  const loadParticipants = useCallback(async () => {
    try {
      const res = await matchesApi.participants(match.id);
      setParticipants(res.data as MatchParticipant[]);
    } catch { /* non-fatal */ }
  }, [match.id]);

  // ─── refresh context + events ────────────────────────────────────────────
  const refresh = useCallback(async () => {
    try {
      const ctxRes = await contextsApi.getTree(match.id);
      const ctxList = ctxRes.data as GameContext[];
      const zoneCtx = ctxList.find((c) => c.context_type === 'zone_map') ?? null;
      setContext(zoneCtx);

      // Find active zone_event context
      const zoneState = zoneCtx?.state_blob as unknown as ZoneMapState | null;
      if (zoneState?.active_events?.length) {
        const activeEvtId = zoneState.active_events[0];
        const evtCtx = ctxList.find((c) => c.id === activeEvtId) ?? null;
        setActiveEventCtx(evtCtx);
        if (evtCtx) setActiveTab('event');
      } else {
        setActiveEventCtx(null);
      }

      const evRes = await eventsApi.listForMatch(match.id);
      setEvents(evRes.data as GameEvent[]);
    } catch { /* ignore */ }
  }, [match.id]);

  // ─── ensure zone_map context exists ─────────────────────────────────────
  const ensureContext = useCallback(async () => {
    const ctxRes = await contextsApi.getTree(match.id);
    const existing = (ctxRes.data as GameContext[]).find((c) => c.context_type === 'zone_map');
    if (existing) { setContext(existing); return existing; }
    const newCtx = await contextsApi.create({ match_id: match.id, context_type: 'zone_map' });
    setContext(newCtx.data as GameContext);
    return newCtx.data as GameContext;
  }, [match.id]);

  // ─── initial load ────────────────────────────────────────────────────────
  useEffect(() => {
    loadParticipants();
    if (isActive) ensureContext().then(() => refresh());
    else refresh();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [match.id, match.status]);

  // ─── lobby polling ───────────────────────────────────────────────────────
  useEffect(() => {
    if (lobbyPollRef.current) clearInterval(lobbyPollRef.current);
    if (!isWaiting) return;
    lobbyPollRef.current = setInterval(async () => {
      await loadParticipants();
      try {
        const mRes = await matchesApi.get(match.id);
        const updated = mRes.data as Match;
        if (updated.status === 'archived') { onMatchDeleted(match.id); return; }
        if (updated.status !== match.status) onMatchUpdated(updated);
      } catch (e: unknown) {
        if ((e as { response?: { status?: number } })?.response?.status === 404)
          onMatchDeleted(match.id);
      }
    }, 2500);
    return () => { if (lobbyPollRef.current) clearInterval(lobbyPollRef.current); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isWaiting, match.id, match.status]);

  // ─── active polling ──────────────────────────────────────────────────────
  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (isActive && !zoneState?.game_over) {
      pollRef.current = setInterval(async () => {
        try {
          const mRes = await matchesApi.get(match.id);
          const updated = mRes.data as Match;
          if (updated.status === 'archived') { onMatchDeleted(match.id); return; }
          if (updated.status !== match.status) onMatchUpdated(updated);
        } catch (e: unknown) {
          if ((e as { response?: { status?: number } })?.response?.status === 404)
            onMatchDeleted(match.id);
        }
        await refresh();
      }, 5000);
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isActive, zoneState?.game_over, match.id, refresh]);

  // ─── commands ────────────────────────────────────────────────────────────
  const sendCommand = useCallback(async (commandType: string, payload: Record<string, unknown>, contextId?: string) => {
    const ctxId = contextId ?? context?.id;
    if (!ctxId) return;
    setActionLoading(true);
    setError(null);
    try {
      const res = await commandsApi.submit({
        match_id: match.id,
        context_id: ctxId,
        command_type: commandType,
        payload,
      });
      if (res.data.status === 'rejected') {
        setError(res.data.error ?? 'Action rejected');
      }
      await refresh();
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(msg ?? 'Command failed.');
    } finally {
      setActionLoading(false);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [context?.id, match.id, refresh]);

  const handleMove = async (targetLocId: string) => {
    await sendCommand('move_agent', { target_location_id: targetLocId });
    setSelectedLocId(null);
  };

  const handleTravel = async (targetLocId: string) => {
    await sendCommand('travel', { target_location_id: targetLocId });
    setTravelTarget(null);
    setShowTravelPanel(false);
  };

  const handleExplore = async () => sendCommand('explore_location', {});

  const handleSleep = async () => sendCommand('sleep', { hours: sleepHours });

  const handlePickUpArtifact = async (artifactId: string) =>
    sendCommand('pick_up_artifact', { artifact_id: artifactId });

  const handlePickUpItem = async (itemId: string) =>
    sendCommand('pick_up_item', { item_id: itemId });

  const handleConsumeItem = async (itemId: string) =>
    sendCommand('consume_item', { item_id: itemId });

  const handleEndTurn = async () => sendCommand('end_turn', {});

  const handleChooseOption = async (optionIndex: number) => {
    if (!activeEventCtx) return;
    await sendCommand('choose_option', { option_index: optionIndex }, activeEventCtx.id);
  };

  const handleLeaveEvent = async () => {
    if (!activeEventCtx) return;
    await sendCommand('leave_event', {}, activeEventCtx.id);
  };

  const handleManualTick = async () => {
    setActionLoading(true);
    setError(null);
    try {
      await matchesApi.tick(match.id);
      await refresh();
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(msg ?? 'Tick failed.');
    } finally {
      setActionLoading(false);
    }
  };

  const handleJoin = async () => {
    setActionLoading(true);
    setError(null);
    try {
      await matchesApi.join(match.id);
      await loadParticipants();
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      if (msg && !msg.toLowerCase().includes('already')) setError(msg);
    } finally {
      setActionLoading(false);
    }
  };

  const handleStart = async () => {
    setActionLoading(true);
    setError(null);
    try {
      const res = await matchesApi.start(match.id);
      onMatchUpdated(res.data as Match);
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(msg ?? 'Failed to start match.');
    } finally {
      setActionLoading(false);
    }
  };

  const handleDeleteMatch = async () => {
    if (!window.confirm('Close this room?')) return;
    setActionLoading(true);
    try {
      await matchesApi.delete(match.id);
      onMatchDeleted(match.id);
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(msg ?? 'Failed to close room.');
      setActionLoading(false);
    }
  };

  const handleTakeControl = async (agentId: string) => {
    if (!context?.id) return;
    setTakingControlOf(agentId);
    setError(null);
    try {
      const res = await commandsApi.submit({
        match_id: match.id,
        context_id: context.id,
        command_type: 'take_control',
        payload: { agent_id: agentId },
      });
      if (res.data.status === 'rejected') {
        setError(res.data.error ?? 'Take control rejected');
        return;
      }
      await refresh();
      enterGame();
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(msg ?? 'Take control failed.');
    } finally {
      setTakingControlOf(null);
    }
  };

  const alreadyJoined = participants.some((p) => p.user_id === user.id);

  // ─── render: entry menu ──────────────────────────────────────────────────
  const renderEntryMenu = () => {
    const hasMyAgent = !!myAgentId;
    const worldInfo = zoneState
      ? `День ${zoneState.world_day} · ${TIME_LABEL(zoneState.world_hour, zoneState.world_minute ?? 0)} · Ход ${zoneState.world_turn}${zoneState.max_turns ? `/${zoneState.max_turns}` : ''}`
      : 'Загрузка…';

    return (
      <div style={styles.entryPage}>
        {/* Ambient glow background */}
        <div style={styles.entryBg} />

        <div style={styles.entryCard}>
          {/* Title */}
          <div style={styles.entryLogo}>☢️</div>
          <h2 style={styles.entryTitle}>ZONE STALKERS</h2>
          <div style={styles.entrySubtitle}>{worldInfo}</div>

          <div style={styles.entrySep} />

          {/* Menu buttons */}
          <button
            style={styles.entryBtn}
            onClick={() => setEntryScreen('npc_select')}
            disabled={!zoneState}
          >
            <span style={styles.entryBtnIcon}>🤖</span>
            <span style={styles.entryBtnText}>
              <span style={styles.entryBtnLabel}>Играть за существующего НПЦ</span>
              <span style={styles.entryBtnHint}>Взять под контроль NPC-сталкера</span>
            </span>
          </button>

          <button
            style={{
              ...styles.entryBtn,
              ...(hasMyAgent ? styles.entryBtnPrimary : {}),
            }}
            onClick={enterGame}
            disabled={!hasMyAgent}
            title={hasMyAgent ? undefined : 'Нет назначенного персонажа'}
          >
            <span style={styles.entryBtnIcon}>👤</span>
            <span style={styles.entryBtnText}>
              <span style={styles.entryBtnLabel}>
                {hasMyAgent
                  ? `Играть за ${zoneState?.agents?.[myAgentId]?.name ?? 'сталкера'}`
                  : 'Играть за нового персонажа'}
              </span>
              <span style={styles.entryBtnHint}>
                {hasMyAgent ? 'Ваш назначенный сталкер' : 'Нет назначенного персонажа'}
              </span>
            </span>
          </button>

          <button
            style={{ ...styles.entryBtn, ...styles.entryBtnDebug }}
            onClick={enterAsDebug}
            disabled={!zoneState}
          >
            <span style={styles.entryBtnIcon}>🔧</span>
            <span style={styles.entryBtnText}>
              <span style={styles.entryBtnLabel}>Дебаг</span>
              <span style={styles.entryBtnHint}>Просмотр и редактирование игрового мира</span>
            </span>
          </button>

          {error && <p style={styles.error}>{error}</p>}
        </div>
      </div>
    );
  };

  // ─── render: npc select ──────────────────────────────────────────────────
  const renderNpcSelect = () => {
    if (!zoneState) return <p style={styles.loadingText}>Загрузка…</p>;

    // Only show alive AI-controlled stalkers
    const aiStalkers = Object.values(zoneState.agents).filter(
      (a) => a.is_alive && a.controller.kind === 'bot',
    );

    return (
      <div style={styles.npcSelectPage}>
        <div style={styles.npcSelectHeader}>
          <button style={styles.npcBackBtn} onClick={() => setEntryScreen('main')}>
            ← Назад
          </button>
          <h3 style={styles.npcSelectTitle}>🤖 Выбор NPC-сталкера</h3>
          <span style={styles.npcSelectHint}>
            Выберите NPC, за которого хотите играть. Вы возьмёте под контроль его агента.
          </span>
        </div>

        {aiStalkers.length === 0 ? (
          <div style={styles.npcEmpty}>
            <p>Нет свободных NPC-сталкеров.</p>
            <p style={{ color: '#475569', fontSize: '0.8rem' }}>
              Все сталкеры уже под контролем игроков или мертвы.
            </p>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {aiStalkers.map((agent) => (
              <AgentRow
                key={agent.id}
                agent={agent as unknown as AgentForProfile}
                locations={zoneState.locations}
                onTakeControl={() => handleTakeControl(agent.id)}
              />
            ))}
          </div>
        )}

        {error && <p style={styles.error}>{error}</p>}
      </div>
    );
  };

  // ─── render: lobby ───────────────────────────────────────────────────────
  const renderLobby = () => {
    const canStart = isCreator && participants.length >= 1;
    return (
      <div style={styles.lobby}>
        <span style={styles.lobbyEmoji}>☢️</span>
        <h3 style={styles.lobbyTitle}>Zone Stalkers — Waiting Room</h3>
        <p style={styles.lobbyHint}>
          {participants.length} player{participants.length !== 1 ? 's' : ''} in the Zone
        </p>
        <div style={styles.participantList}>
          {participants.map((p) => (
            <div key={p.id} style={styles.participantBadge}>
              👤 {p.display_name ?? p.user_id?.slice(0, 8) ?? 'Unknown'}
            </div>
          ))}
        </div>
        <p style={styles.lobbyNote}>
          Zone Stalkers can be played solo or with multiple players. Each player gets their own stalker agent.
          The game runs in <strong>real time</strong> — 1 game-hour per tick.
        </p>
        <div style={styles.lobbyActions}>
          {!alreadyJoined && (
            <button style={styles.btnPrimary} onClick={handleJoin} disabled={actionLoading}>
              {actionLoading ? '…' : 'Join the Zone'}
            </button>
          )}
          {isCreator && (
            <button
              style={{ ...styles.btnPrimary, ...(canStart ? {} : styles.btnDisabled) }}
              onClick={handleStart}
              disabled={actionLoading || !canStart}
            >
              {actionLoading ? '…' : 'Enter the Zone'}
            </button>
          )}
          {isCreator && (
            <button style={styles.btnDanger} onClick={handleDeleteMatch} disabled={actionLoading}>
              {actionLoading ? '…' : 'Close Room'}
            </button>
          )}
        </div>
        {error && <p style={styles.error}>{error}</p>}
      </div>
    );
  };

  // ─── render: scheduled action status ────────────────────────────────────
  const renderScheduledAction = () => {
    if (!myAgent?.scheduled_action) return null;
    const sa = myAgent.scheduled_action;
    const icon = SCHED_ICONS[sa.type] ?? '⏳';
    const label =
      sa.type === 'travel'
        ? `Travelling to ${zoneState?.locations?.[sa.target_id]?.name ?? sa.target_id}`
        : sa.type === 'explore'
        ? `Exploring ${zoneState?.locations?.[sa.target_id]?.name ?? 'location'}`
        : sa.type === 'sleep'
        ? `Sleeping (${sa.turns_remaining}h remaining)`
        : sa.type === 'event'
        ? 'Participating in event'
        : `${sa.type} in progress`;
    const pct = ((sa.turns_total - sa.turns_remaining) / sa.turns_total) * 100;
    return (
      <div style={styles.schedCard}>
        <div style={styles.schedRow}>
          <span style={styles.schedIcon}>{icon}</span>
          <span style={styles.schedLabel}>{label}</span>
          <span style={styles.schedTurns}>{sa.turns_remaining}h left</span>
        </div>
        <div style={styles.progressBg}>
          <div style={{ ...styles.progressFill, width: `${pct}%` }} />
        </div>
        <p style={styles.schedHint}>Use "⏭ End Turn" to advance time — or wait for the auto-ticker.</p>
      </div>
    );
  };

  // ─── render: action panel ────────────────────────────────────────────────
  const renderActionPanel = () => {
    if (!myAgent) return null;
    const loc = currentLoc;
    const isSafe = (loc?.anomaly_activity ?? 0) <= 3;
    const scheduled = !!myAgent.scheduled_action;

    return (
      <div style={styles.actionPanel}>
        <div style={styles.panelTitle}>⚡ Actions</div>
        {renderScheduledAction()}
        {!scheduled && canAct && (
          <>
            {/* Explore */}
            <button
              style={styles.actionBtn}
              onClick={handleExplore}
              disabled={actionLoading}
              title="Spend 1 hour searching this location"
            >
              🔍 Explore (1h)
            </button>

            {/* Travel */}
            <button
              style={styles.actionBtn}
              onClick={() => setShowTravelPanel(!showTravelPanel)}
              disabled={actionLoading}
              title="Plan a multi-hour journey"
            >
              🚶 Travel…
            </button>

            {/* Sleep */}
            {isSafe && (
              <div style={styles.sleepRow}>
                <button
                  style={styles.actionBtn}
                  onClick={handleSleep}
                  disabled={actionLoading}
                  title="Rest to recover HP and reduce radiation"
                >
                  😴 Sleep
                </button>
                <select
                  style={styles.sleepSelect}
                  value={sleepHours}
                  onChange={(e) => setSleepHours(Number(e.target.value))}
                >
                  {[2, 4, 6, 8, 10].map((h) => (
                    <option key={h} value={h}>{h}h</option>
                  ))}
                </select>
              </div>
            )}

            {/* Active events */}
            {zoneState?.active_events?.map((evtId) => (
              <button
                key={evtId}
                style={{ ...styles.actionBtn, background: '#312e81', borderColor: '#4f46e5' }}
                onClick={() => sendCommand('join_event', { event_context_id: evtId })}
                disabled={actionLoading}
              >
                📖 Join Event
              </button>
            ))}
          </>
        )}

        {/* Travel panel */}
        {showTravelPanel && canAct && (
          <div style={styles.travelPanel}>
            <div style={styles.panelTitle}>Select destination:</div>
            {Object.values(zoneState?.locations ?? {})
              .filter((l) => l.id !== currentLocId)
              .map((loc) => (
                <button
                  key={loc.id}
                  style={{
                    ...styles.travelLocBtn,
                    ...(travelTarget === loc.id ? styles.travelLocBtnSelected : {}),
                  }}
                  onClick={() => {
                    if (travelTarget === loc.id) {
                      handleTravel(loc.id);
                    } else {
                      setTravelTarget(loc.id);
                    }
                  }}
                >
                  {loc.name}
                  {(loc.anomaly_activity ?? 0) > 0 && <span style={styles.travelDanger}> ☢ {loc.anomaly_activity}</span>}
                  {travelTarget === loc.id && <span style={{ color: '#60a5fa' }}> → Go!</span>}
                </button>
              ))}
            <button style={styles.cancelBtn} onClick={() => { setShowTravelPanel(false); setTravelTarget(null); }}>
              Cancel
            </button>
          </div>
        )}

        {/* End turn always available */}
        <button
          style={styles.endTurnBtn}
          onClick={handleEndTurn}
          disabled={actionLoading}
          title="Advance time by 1 hour"
        >
          {actionLoading ? '…' : '⏭ End Turn (1h)'}
        </button>

        {/* Manual tick (dev helper) */}
        <button
          style={styles.tickBtn}
          onClick={handleManualTick}
          disabled={actionLoading}
          title="Force a world tick (admin/dev)"
        >
          {actionLoading ? '…' : '⚙ Tick World'}
        </button>

        {error && <p style={styles.error}>{error}</p>}
      </div>
    );
  };

  // ─── render: agent profile (full detail view) ───────────────────────────
  const renderAgentProfile = (agentId: string) => {
    if (!zoneState) return null;

    // Find the entity — could be stalker, mutant, or trader
    const stalker = zoneState.agents[agentId];
    const mutant = !stalker ? zoneState.mutants[agentId] : null;
    const trader = !stalker && !mutant
      ? Object.values(zoneState.traders).find((t) => t.id === agentId) ?? null
      : null;

    const locName = (locId: string) => zoneState.locations[locId]?.name ?? locId;

    return (
      <div style={styles.profileOverlay}>
        <div style={styles.profileModal}>
          {/* ── Header ── */}
          <div style={styles.profileHeader}>
            <button style={styles.profileBackBtn} onClick={() => setProfileAgentId(null)}>
              ← Back to Roster
            </button>
            {profileAgentId === myAgentId && (
              <button style={styles.profileEnterBtn} onClick={() => { enterGame(); setProfileAgentId(null); }}>
                ▶ Play as this Stalker
              </button>
            )}
          </div>

          {stalker && (
            <>
              <div style={styles.profileTitle}>
                <span style={styles.profileAvatar}>{stalker.controller.kind === 'human' ? '👤' : '🤖'}</span>
                <div>
                  <div style={styles.profileName}>{stalker.name}</div>
                  <div style={styles.profileSubtitle}>
                    {stalker.faction} · {stalker.controller.kind === 'human' ? 'Player' : 'NPC'} ·
                    {' '}{stalker.is_alive ? '🟢 Alive' : '💀 Dead'}
                  </div>
                </div>
              </div>

              {/* ── Location ── */}
              <div style={styles.profileSection}>
                <div style={styles.profileSectionLabel}>📍 Location</div>
                <div style={styles.profileSectionVal}>{locName(stalker.location_id)}</div>
                {stalker.scheduled_action && (
                  <div style={styles.profileSched}>
                    {SCHED_ICONS[stalker.scheduled_action.type] ?? '⏳'} {stalker.scheduled_action.type}
                    {' '}— {stalker.scheduled_action.turns_remaining}h remaining
                  </div>
                )}
              </div>

              {/* ── Vital Stats ── */}
              <div style={styles.profileSection}>
                <div style={styles.profileSectionLabel}>📊 Vital Stats</div>
                {[
                  { label: '❤️ HP', val: stalker.hp, max: stalker.max_hp, pct: stalker.max_hp > 0 ? stalker.hp / stalker.max_hp : 0, color: stalker.hp > 50 ? '#22c55e' : stalker.hp > 25 ? '#f59e0b' : '#ef4444' },
                  { label: '☢ Rad', val: stalker.radiation, max: 100, pct: Math.min(stalker.radiation, 100) / 100, color: '#a855f7' },
                  { label: '🍖 Hunger', val: stalker.hunger ?? 0, max: 100, pct: (stalker.hunger ?? 0) / 100, color: (stalker.hunger ?? 0) > 75 ? '#ef4444' : '#22c55e' },
                  { label: '💧 Thirst', val: stalker.thirst ?? 0, max: 100, pct: (stalker.thirst ?? 0) / 100, color: (stalker.thirst ?? 0) > 75 ? '#ef4444' : '#3b82f6' },
                  { label: '😴 Sleep', val: stalker.sleepiness ?? 0, max: 100, pct: (stalker.sleepiness ?? 0) / 100, color: (stalker.sleepiness ?? 0) > 75 ? '#ef4444' : '#64748b' },
                ].map(({ label, val, max, pct, color }) => (
                  <div key={label} style={styles.profileStatRow}>
                    <span style={styles.profileStatLabel}>{label}</span>
                    <div style={styles.barBg}>
                      <div style={{ ...styles.barFill, width: `${max > 0 ? pct * 100 : 0}%`, background: color }} />
                    </div>
                    <span style={styles.profileStatVal}>{val}/{max}</span>
                  </div>
                ))}
                <div style={styles.profileMoney}>💰 {stalker.money} RU</div>
                {stalker.reputation != null && <div style={styles.profileRep}>⭐ Reputation: {stalker.reputation}</div>}
              </div>

              {/* ── Skills ── */}
              {(stalker.skill_combat != null) && (
                <div style={styles.profileSection}>
                  <div style={styles.profileSectionLabel}>🎯 Skills</div>
                  <div style={styles.profileSkillGrid}>
                    {[
                      { label: '⚔ Combat', val: stalker.skill_combat ?? 1 },
                      { label: '🔭 Stalker', val: stalker.skill_stalker ?? 1 },
                      { label: '💼 Trade', val: stalker.skill_trade ?? 1 },
                      { label: '💊 Medicine', val: stalker.skill_medicine ?? 1 },
                      { label: '🗣 Social', val: stalker.skill_social ?? 1 },
                    ].map(({ label, val }) => (
                      <div key={label} style={styles.profileSkillChip}>
                        <span style={styles.profileSkillLabel}>{label}</span>
                        <span style={styles.profileSkillVal}>Lv {val}</span>
                      </div>
                    ))}
                  </div>
                  {stalker.global_goal && (
                    <div style={styles.profileGoal}>
                      🎯 Goal: <strong>{stalker.global_goal}</strong>
                      {stalker.current_goal && <span style={styles.profileSubgoal}> → {stalker.current_goal}</span>}
                    </div>
                  )}
                </div>
              )}

              {/* ── Equipment ── */}
              <div style={styles.profileSection}>
                <div style={styles.profileSectionLabel}>🔫 Equipment</div>
                {Object.entries(stalker.equipment).map(([slot, item]) => (
                  <div key={slot} style={styles.profileEquipRow}>
                    <span style={styles.profileEquipSlot}>{slot}</span>
                    <span style={item ? styles.profileEquipItem : styles.profileEquipEmpty}>
                      {item ? item.name : '—'}
                    </span>
                    {item?.value != null && <span style={styles.itemVal}>{item.value} RU</span>}
                  </div>
                ))}
              </div>

              {/* ── Inventory ── */}
              <div style={styles.profileSection}>
                <div style={styles.profileSectionLabel}>🎒 Inventory ({stalker.inventory.length} items)</div>
                {stalker.inventory.length === 0
                  ? <div style={styles.emptyText}>Empty</div>
                  : stalker.inventory.map((item) => (
                    <div key={item.id} style={styles.profileInvRow}>
                      <span style={styles.itemName}>{item.name}</span>
                      {item.weight != null && <span style={styles.profileInvWeight}>{item.weight}kg</span>}
                      {item.value != null && <span style={styles.itemVal}>{item.value} RU</span>}
                    </div>
                  ))}
              </div>

              {/* ── Memory ── */}
              {stalker.memory.length > 0 && (
                <div style={styles.profileSection}>
                  <div style={styles.profileSectionLabel}>🧠 Memory ({stalker.memory.length} entries)</div>
                  <div style={styles.profileMemoryList}>
                    {[...stalker.memory].reverse().slice(0, 10).map((m, i) => (
                      <div key={i} style={styles.memoryEntry}>
                        <div style={styles.memoryHeader}>
                          <span style={styles.memoryType}>{SCHED_ICONS[m.type] ?? '📝'} {m.type}</span>
                          <span style={styles.memoryWhen}>Day {m.world_day} · {TIME_LABEL(m.world_hour, m.world_minute ?? 0)}</span>
                        </div>
                        <div style={styles.memoryTitle}>{m.title}</div>
                        <div style={styles.memorySummary}>{m.summary}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}

          {mutant && (
            <>
              <div style={styles.profileTitle}>
                <span style={styles.profileAvatar}>☣️</span>
                <div>
                  <div style={styles.profileName}>{mutant.name}</div>
                  <div style={styles.profileSubtitle}>Mutant · {mutant.is_alive ? '🟢 Alive' : '💀 Dead'}</div>
                </div>
              </div>
              <div style={styles.profileSection}>
                <div style={styles.profileSectionLabel}>📍 Location</div>
                <div style={styles.profileSectionVal}>{locName(mutant.location_id)}</div>
              </div>
              <div style={styles.profileSection}>
                <div style={styles.profileSectionLabel}>📊 Stats</div>
                <div style={styles.profileStatRow}>
                  <span style={styles.profileStatLabel}>❤️ HP</span>
                  <div style={styles.barBg}>
                    <div style={{ ...styles.barFill, width: `${mutant.max_hp > 0 ? (mutant.hp / mutant.max_hp) * 100 : 0}%`, background: '#ef4444' }} />
                  </div>
                  <span style={styles.profileStatVal}>{mutant.hp}/{mutant.max_hp}</span>
                </div>
              </div>
            </>
          )}

          {trader && (
            <>
              <div style={styles.profileTitle}>
                <span style={styles.profileAvatar}>🏪</span>
                <div>
                  <div style={styles.profileName}>{(trader as { name: string }).name}</div>
                  <div style={styles.profileSubtitle}>Trader</div>
                </div>
              </div>
              <div style={styles.profileSection}>
                <div style={styles.profileSectionLabel}>📍 Location</div>
                <div style={styles.profileSectionVal}>{locName((trader as { location_id: string }).location_id)}</div>
              </div>
            </>
          )}

          {!stalker && !mutant && !trader && (
            <div style={styles.emptyText}>Character data not found.</div>
          )}
        </div>
      </div>
    );
  };

  // ─── render: character roster ────────────────────────────────────────────
  const renderRoster = () => {
    if (!zoneState) return <p style={styles.loadingText}>Loading roster…</p>;

    const allStalkers = Object.values(zoneState.agents);
    const allMutants = Object.values(zoneState.mutants);
    const allTraders = Object.values(zoneState.traders);

    const locName = (locId: string) => zoneState.locations[locId]?.name ?? locId;

    const renderStatBar = (val: number, max: number, color: string) => (
      <div style={styles.barBg}>
        <div style={{ ...styles.barFill, width: `${max > 0 ? (val / max) * 100 : 0}%`, background: color }} />
      </div>
    );

    return (
      <div style={styles.rosterPage}>
        {/* Profile modal */}
        {profileAgentId && renderAgentProfile(profileAgentId)}

        {/* Header */}
        <div style={styles.rosterHeader}>
          <div>
            <h3 style={styles.rosterTitle}>☢️ Zone Stalkers — Character Roster</h3>
            <p style={styles.rosterSubtitle}>
              Day {zoneState.world_day} · {TIME_LABEL(zoneState.world_hour, zoneState.world_minute ?? 0)} · Turn {zoneState.world_turn}{zoneState.max_turns ? `/${zoneState.max_turns}` : ''}
            </p>
          </div>
          {myAgentId && (
            <button style={styles.rosterEnterBtn} onClick={enterGame}>
              ▶ Enter Game as {zoneState.agents[myAgentId]?.name ?? 'Stalker'}
            </button>
          )}
        </div>

        {/* ── Stalkers ── */}
        {allStalkers.length > 0 && (
          <div style={styles.rosterSection}>
            <div style={styles.rosterSectionTitle}>👤 Stalkers ({allStalkers.length})</div>
            <div style={styles.rosterGrid}>
              {allStalkers.map((agent) => {
                const isMe = agent.id === myAgentId;
                return (
                  <div
                    key={agent.id}
                    style={{ ...styles.rosterCard, ...(isMe ? styles.rosterCardMe : {}) }}
                  >
                    <div style={styles.rosterCardTop}>
                      <span style={styles.rosterCardAvatar}>
                        {isMe ? '⭐' : agent.controller.kind === 'human' ? '👤' : '🤖'}
                      </span>
                      <div style={styles.rosterCardInfo}>
                        <div style={styles.rosterCardName}>{agent.name}</div>
                        <div style={styles.rosterCardSub}>
                          {agent.faction} · {isMe ? 'You' : agent.controller.kind === 'human' ? 'Player' : 'NPC'}
                        </div>
                      </div>
                      <span style={{
                        ...styles.rosterAliveTag,
                        background: agent.is_alive ? '#166534' : '#7f1d1d',
                        color: agent.is_alive ? '#86efac' : '#fca5a5',
                      }}>
                        {agent.is_alive ? '🟢' : '💀'}
                      </span>
                    </div>

                    <div style={styles.rosterStatRow}>
                      <span style={styles.rosterStatLabel}>❤️</span>
                      {renderStatBar(agent.hp, agent.max_hp, agent.hp > 50 ? '#22c55e' : agent.hp > 25 ? '#f59e0b' : '#ef4444')}
                      <span style={styles.rosterStatVal}>{agent.hp}</span>
                    </div>
                    {agent.radiation > 0 && (
                      <div style={styles.rosterStatRow}>
                        <span style={styles.rosterStatLabel}>☢</span>
                        {renderStatBar(Math.min(agent.radiation, 100), 100, '#a855f7')}
                        <span style={styles.rosterStatVal}>{agent.radiation}</span>
                      </div>
                    )}

                    <div style={styles.rosterLocation}>📍 {locName(agent.location_id)}</div>
                    {agent.scheduled_action && (
                      <div style={styles.rosterSched}>
                        {SCHED_ICONS[agent.scheduled_action.type] ?? '⏳'} {agent.scheduled_action.type} ({agent.scheduled_action.turns_remaining}h)
                      </div>
                    )}
                    <div style={styles.rosterCardFooter}>
                      <span style={styles.rosterMoney}>💰 {agent.money} RU</span>
                      <button
                        style={styles.rosterViewBtn}
                        onClick={() => setProfileAgentId(agent.id)}
                      >
                        View Profile
                      </button>
                      {isMe && (
                        <button
                          style={styles.rosterPlayBtn}
                          onClick={enterGame}
                        >
                          ▶ Play
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* ── Mutants ── */}
        {allMutants.length > 0 && (
          <div style={styles.rosterSection}>
            <div style={styles.rosterSectionTitle}>☣️ Mutants ({allMutants.length})</div>
            <div style={styles.rosterGrid}>
              {allMutants.map((mutant) => (
                <div key={mutant.id} style={styles.rosterCard}>
                  <div style={styles.rosterCardTop}>
                    <span style={styles.rosterCardAvatar}>☣️</span>
                    <div style={styles.rosterCardInfo}>
                      <div style={styles.rosterCardName}>{mutant.name}</div>
                      <div style={styles.rosterCardSub}>Mutant</div>
                    </div>
                    <span style={{
                      ...styles.rosterAliveTag,
                      background: mutant.is_alive ? '#7f1d1d' : '#1e293b',
                      color: mutant.is_alive ? '#fca5a5' : '#475569',
                    }}>
                      {mutant.is_alive ? '🔴' : '💀'}
                    </span>
                  </div>
                  <div style={styles.rosterStatRow}>
                    <span style={styles.rosterStatLabel}>❤️</span>
                    {renderStatBar(mutant.hp, mutant.max_hp, '#ef4444')}
                    <span style={styles.rosterStatVal}>{mutant.hp}</span>
                  </div>
                  <div style={styles.rosterLocation}>📍 {locName(mutant.location_id)}</div>
                  <div style={styles.rosterCardFooter}>
                    <button style={styles.rosterViewBtn} onClick={() => setProfileAgentId(mutant.id)}>
                      View Profile
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── Traders ── */}
        {allTraders.length > 0 && (
          <div style={styles.rosterSection}>
            <div style={styles.rosterSectionTitle}>🏪 Traders ({allTraders.length})</div>
            <div style={styles.rosterGrid}>
              {allTraders.map((trader) => (
                <div key={trader.id} style={styles.rosterCard}>
                  <div style={styles.rosterCardTop}>
                    <span style={styles.rosterCardAvatar}>🏪</span>
                    <div style={styles.rosterCardInfo}>
                      <div style={styles.rosterCardName}>{trader.name}</div>
                      <div style={styles.rosterCardSub}>Trader</div>
                    </div>
                  </div>
                  <div style={styles.rosterLocation}>📍 {locName(trader.location_id)}</div>
                  <div style={styles.rosterCardFooter}>
                    <button style={styles.rosterViewBtn} onClick={() => setProfileAgentId(trader.id)}>
                      View Profile
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  };

  // ─── render: zone map ────────────────────────────────────────────────────
  const renderZoneMap = () => {
    if (!zoneState) return <p style={styles.loadingText}>Generating the Zone…</p>;
    const locations = Object.values(zoneState.locations);

    return (
      <div style={styles.mapContainer}>
        {/* ── Agent status panel ── */}
        <div style={styles.agentPanel}>
          <div style={styles.panelTitle}>☢️ Your Stalker</div>
          {myAgent ? (
            <>
              <div style={styles.agentName}>{myAgent.name}</div>
              <div style={styles.agentFaction}>Faction: {myAgent.faction}</div>
              <div style={styles.statRow}>
                <span style={styles.statLabel}>❤️ HP</span>
                <div style={styles.barBg}>
                  <div style={{ ...styles.barFill, width: `${(myAgent.hp / myAgent.max_hp) * 100}%`, background: myAgent.hp > 50 ? '#22c55e' : myAgent.hp > 25 ? '#f59e0b' : '#ef4444' }} />
                </div>
                <span style={styles.statVal}>{myAgent.hp}/{myAgent.max_hp}</span>
              </div>
              <div style={styles.statRow}>
                <span style={styles.statLabel}>☢ Rad</span>
                <div style={styles.barBg}>
                  <div style={{ ...styles.barFill, width: `${Math.min(myAgent.radiation, 100)}%`, background: '#a855f7' }} />
                </div>
                <span style={styles.statVal}>{myAgent.radiation}</span>
              </div>
              {/* Survival needs */}
              <div style={styles.statRow}>
                <span style={styles.statLabel}>🍖</span>
                <div style={styles.barBg}>
                  <div style={{ ...styles.barFill, width: `${myAgent.hunger ?? 0}%`, background: (myAgent.hunger ?? 0) > 75 ? '#ef4444' : (myAgent.hunger ?? 0) > 50 ? '#f59e0b' : '#22c55e' }} />
                </div>
                <span style={styles.statVal}>{myAgent.hunger ?? 0}</span>
              </div>
              <div style={styles.statRow}>
                <span style={styles.statLabel}>💧</span>
                <div style={styles.barBg}>
                  <div style={{ ...styles.barFill, width: `${myAgent.thirst ?? 0}%`, background: (myAgent.thirst ?? 0) > 75 ? '#ef4444' : (myAgent.thirst ?? 0) > 50 ? '#f59e0b' : '#3b82f6' }} />
                </div>
                <span style={styles.statVal}>{myAgent.thirst ?? 0}</span>
              </div>
              <div style={styles.statRow}>
                <span style={styles.statLabel}>😴</span>
                <div style={styles.barBg}>
                  <div style={{ ...styles.barFill, width: `${myAgent.sleepiness ?? 0}%`, background: (myAgent.sleepiness ?? 0) > 75 ? '#ef4444' : (myAgent.sleepiness ?? 0) > 50 ? '#f59e0b' : '#64748b' }} />
                </div>
                <span style={styles.statVal}>{myAgent.sleepiness ?? 0}</span>
              </div>
              <div style={styles.moneyRow}>💰 {myAgent.money} RU</div>
              <div style={styles.locationLabel}>
                📍 {zoneState.locations[myAgent.location_id]?.name ?? myAgent.location_id}
              </div>
              <div style={styles.turnInfo}>
                Day {zoneState.world_day} · {TIME_LABEL(zoneState.world_hour, zoneState.world_minute ?? 0)} · Turn {zoneState.world_turn}{zoneState.max_turns ? `/${zoneState.max_turns}` : ''}
                {myAgent.action_used && !myAgent.scheduled_action && (
                  <span style={styles.actionUsedBadge}>Acted</span>
                )}
                {myAgent.scheduled_action && (
                  <span style={{ ...styles.actionUsedBadge, background: '#1e3a5f', color: '#60a5fa' }}>
                    {SCHED_ICONS[myAgent.scheduled_action.type]} {myAgent.scheduled_action.type}
                  </span>
                )}
              </div>

              {/* Inventory */}
              <div style={styles.inventoryTitle}>🎒 Inventory ({myAgent.inventory.length})</div>
              <div style={styles.inventoryList}>
                {myAgent.inventory.length === 0
                  ? <span style={styles.emptyText}>Empty</span>
                  : myAgent.inventory.map((item) => {
                    const isConsumable = ['medkit', 'bandage', 'antirad', 'bread', 'energy_drink', 'vodka'].includes(item.type);
                    return (
                      <div key={item.id} style={styles.inventoryItem}>
                        <span style={styles.itemName}>{item.name}</span>
                        {item.value != null && <span style={styles.itemVal}>{item.value} RU</span>}
                        {isConsumable && canAct && (
                          <button
                            style={styles.useItemBtn}
                            onClick={() => handleConsumeItem(item.id)}
                            disabled={actionLoading}
                            title={`Use ${item.name}`}
                          >
                            Use
                          </button>
                        )}
                      </div>
                    );
                  })}
              </div>
            </>
          ) : (
            <p style={styles.emptyText}>No agent assigned yet.</p>
          )}
        </div>

        {/* ── Center: tabs (map / event / memory) ── */}
        <div style={styles.centerPanel}>
          <div style={styles.tabBar}>
            {(['map', 'event', 'memory'] as const).map((tab) => (
              <button
                key={tab}
                style={{ ...styles.tabBtn, ...(activeTab === tab ? styles.tabBtnActive : {}) }}
                onClick={() => setActiveTab(tab)}
              >
                {tab === 'map' ? '🗺 Map' : tab === 'event' ? `📖 Event${eventState?.phase === 'active' ? ' ●' : ''}` : '🧠 Memory'}
              </button>
            ))}
            <button
              style={{ ...styles.tabBtn, ...styles.debugTabBtn }}
              onClick={() => { setShowDebug(true); setShowEntryMenu(false); setDebugTab('map'); }}
              title="Open full debug panel"
            >
              🔧 Debug
            </button>
            <button
              style={{ ...styles.tabBtn, ...styles.rosterTabBtn }}
              onClick={() => setShowRoster(true)}
              title="View all characters in the Zone"
            >
              👥 Roster
            </button>
          </div>

          {activeTab === 'map' && (
            <>
              <p style={styles.mapHint}>
                {canAct
                  ? 'Click a connected location once to select, twice to move instantly.'
                  : myAgent?.scheduled_action
                  ? `${SCHED_ICONS[myAgent.scheduled_action.type]} ${myAgent.scheduled_action.type} in progress — ${myAgent.scheduled_action.turns_remaining}h remaining.`
                  : 'Waiting for turn.'}
              </p>
              <div style={styles.locationGrid}>
                {locations.map((loc) => {
                  const isCurrentLoc = loc.id === currentLocId;
                  const isConnected = currentLoc?.connections.some((c) => c.to === loc.id) ?? false;
                  const isSelected = loc.id === selectedLocId;
                  const agentHere = zoneState
                    ? loc.agents.filter((aid) => zoneState.agents[aid]?.is_alive).length +
                      loc.agents.filter((aid) => zoneState.mutants[aid]?.is_alive).length
                    : 0;
                  const tradersHere = Object.values(zoneState?.traders ?? {}).filter((t) => t.location_id === loc.id).length;

                  return (
                    <div
                      key={loc.id}
                      style={{
                        ...styles.locationCard,
                        ...(isCurrentLoc ? styles.locationCurrent : {}),
                        ...(isConnected && canAct ? styles.locationReachable : {}),
                        ...(isSelected ? styles.locationSelected : {}),
                        cursor: isConnected && canAct ? 'pointer' : 'default',
                        borderLeftColor: TERRAIN_TYPE_COLOR[loc.terrain_type ?? ''] ?? '#475569',
                      }}
                      onClick={() => {
                        if (isConnected && canAct) {
                          if (isSelected) handleMove(loc.id);
                          else setSelectedLocId(loc.id);
                        }
                      }}
                    >
                      <div style={styles.locRow}>
                        <span style={styles.locName}>{loc.name}</span>
                        {(loc.anomaly_activity ?? 0) > 0 && (
                          <span style={{ ...styles.dangerBadge, background: '#7c3aed' }}>
                            ☢ {loc.anomaly_activity}
                          </span>
                        )}
                      </div>
                      <div style={styles.locType}>{TERRAIN_TYPE_LABELS[loc.terrain_type ?? ''] ?? (loc.terrain_type ?? '—')}</div>
                      <div style={styles.locIcons}>
                        {isCurrentLoc && <span style={styles.locBadgeSelf}>📍 You</span>}
                        {agentHere > 0 && <span style={styles.locBadge}>👥 {agentHere}</span>}
                        {tradersHere > 0 && <span style={styles.locBadge}>🏪 Trader</span>}
                        {loc.artifacts.length > 0 && <span style={styles.locBadgeArt}>💎 {loc.artifacts.length}</span>}
                        {loc.anomalies.length > 0 && <span style={styles.locBadgeAnom}>☢ {loc.anomalies.length}</span>}
                      </div>
                      {isSelected && isConnected && <div style={styles.moveHint}>Click again to move →</div>}
                    </div>
                  );
                })}
              </div>

              {/* Current location details */}
              {currentLoc && (
                <div style={styles.locDetail}>
                  <div style={styles.locDetailTitle}>📍 {currentLoc.name}</div>
                  {currentLoc.artifacts.length > 0 && (
                    <div style={styles.locDetailSection}>
                      <div style={styles.locDetailLabel}>💎 Artifacts</div>
                      {currentLoc.artifacts.map((art) => (
                        <div key={art.id} style={styles.locDetailItem}>
                          <span>{art.name}</span>
                          <span style={styles.itemVal}>{art.value} RU</span>
                          {canAct && (
                            <button style={styles.pickUpBtn} onClick={() => handlePickUpArtifact(art.id)} disabled={actionLoading}>
                              Pick up
                            </button>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                  {currentLoc.items.length > 0 && (
                    <div style={styles.locDetailSection}>
                      <div style={styles.locDetailLabel}>📦 Items</div>
                      {currentLoc.items.map((item) => (
                        <div key={item.id} style={styles.locDetailItem}>
                          <span>{item.name}</span>
                          {canAct && (
                            <button style={styles.pickUpBtn} onClick={() => handlePickUpItem(item.id)} disabled={actionLoading}>
                              Pick up
                            </button>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                  {currentLoc.anomalies.length > 0 && (
                    <div style={styles.locDetailSection}>
                      <div style={styles.locDetailLabel}>☢ Anomalies</div>
                      {currentLoc.anomalies.map((a) => (
                        <div key={a.id} style={{ ...styles.locDetailItem, color: '#a855f7' }}>{a.name}</div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </>
          )}

          {activeTab === 'event' && (
            <div style={styles.eventPanel}>
              {!eventState ? (
                <p style={styles.emptyText}>No active events. Explore the Zone to find them.</p>
              ) : (
                <>
                  <div style={styles.eventTitle}>📖 {eventState.title}</div>
                  <div style={{ ...styles.eventPhase, color: eventState.phase === 'ended' ? '#64748b' : '#22c55e' }}>
                    {eventState.phase === 'waiting' ? 'Waiting to start…'
                      : eventState.phase === 'active' ? `Round ${eventState.current_turn} / ${eventState.max_turns}`
                      : 'Concluded'}
                  </div>

                  {/* Narration history */}
                  <div style={styles.narrationHistory}>
                    {eventState.narration_history.map((h) => (
                      <div key={h.turn} style={styles.narrationEntry}>
                        <div style={styles.narrationTurn}>Round {h.turn}</div>
                        <div style={styles.narrationText}>{h.narration}</div>
                        {Object.keys(h.choices).length > 0 && (
                          <div style={styles.choicesSummary}>
                            {Object.entries(h.choices).map(([pid, choice]) => (
                              <span key={pid} style={styles.choiceBadge}>
                                {pid.slice(0, 4)}: {choice}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>

                  {/* Current options */}
                  {eventState.phase === 'active' && eventState.current_options.length > 0 && (
                    <div style={styles.optionsPanel}>
                      <div style={styles.optionsLabel}>
                        {canChooseOption ? 'Choose your action:' : myEventParticipation?.choice != null ? '✓ You chose — waiting for others…' : 'Observing…'}
                      </div>
                      {eventState.current_options.map((opt, i) => (
                        <button
                          key={i}
                          style={{
                            ...styles.optionBtn,
                            ...(myEventParticipation?.choice === i ? styles.optionBtnChosen : {}),
                            ...((!canChooseOption) ? styles.btnDisabled : {}),
                          }}
                          onClick={() => canChooseOption && handleChooseOption(i)}
                          disabled={actionLoading || !canChooseOption}
                        >
                          {i + 1}. {opt}
                        </button>
                      ))}
                    </div>
                  )}

                  {/* Outcome */}
                  {eventState.phase === 'ended' && eventState.outcome && (
                    <div style={styles.outcomeBox}>
                      <div style={styles.outcomeLabel}>📜 Outcome</div>
                      <p style={styles.outcomeText}>{eventState.outcome}</p>
                    </div>
                  )}

                  {/* Leave event */}
                  {eventState.phase !== 'ended' && myEventParticipation?.status === 'active' && (
                    <button style={styles.leaveEventBtn} onClick={handleLeaveEvent} disabled={actionLoading}>
                      Leave Event
                    </button>
                  )}
                </>
              )}
            </div>
          )}

          {activeTab === 'memory' && (
            <div style={styles.memoryPanel}>
              <div style={styles.panelTitle}>🧠 Stalker Memory</div>
              {(!myAgent || myAgent.memory.length === 0) ? (
                <p style={styles.emptyText}>No memories yet. Go explore the Zone!</p>
              ) : (
                <div style={styles.memoryList}>
                  {[...myAgent.memory].reverse().map((m, i) => (
                    <div key={i} style={styles.memoryEntry}>
                      <div style={styles.memoryHeader}>
                        <span style={styles.memoryType}>{SCHED_ICONS[m.type] ?? '📝'} {m.type}</span>
                        <span style={styles.memoryWhen}>Day {m.world_day} · {TIME_LABEL(m.world_hour, m.world_minute ?? 0)}</span>
                      </div>
                      <div style={styles.memoryTitle}>{m.title}</div>
                      <div style={styles.memorySummary}>{m.summary}</div>
                      {Object.keys(m.effects).filter(k => m.effects[k] !== 0).length > 0 && (
                        <div style={styles.memoryEffects}>
                          {Object.entries(m.effects).filter(([, v]) => v !== 0).map(([k, v]) => (
                            <span key={k} style={{ ...styles.effectChip, color: v > 0 ? '#86efac' : '#fca5a5' }}>
                              {k}: {v > 0 ? '+' : ''}{v}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── Right: action panel + event log ── */}
        <div style={styles.rightPanel}>
          {renderActionPanel()}

          <div style={styles.eventsPanel}>
            <div style={styles.panelTitle}>📜 Event Log</div>
            {renderEvents()}
          </div>
        </div>
      </div>
    );
  };

  // ─── render: debug screen ────────────────────────────────────────────────
  const renderDebugScreen = () => {
    if (!zoneState) return <p style={styles.loadingText}>Загрузка…</p>;
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {/* Header with back button and sub-tabs */}
        <div style={styles.debugHeader}>
          <button
            style={styles.btnSmall}
            onClick={() => { setShowDebug(false); setShowEntryMenu(true); }}
          >
            ← Меню
          </button>
          <div style={styles.tabBar}>
            <button
              style={{ ...styles.tabBtn, ...(debugTab === 'map' ? styles.tabBtnActive : {}) }}
              onClick={() => setDebugTab('map')}
            >
              🗺 Карта
            </button>
            <button
              style={{ ...styles.tabBtn, ...(debugTab === 'characters' ? styles.tabBtnActive : {}) }}
              onClick={() => setDebugTab('characters')}
            >
              👥 Персонажи
            </button>
            <button
              style={{ ...styles.tabBtn, ...(debugTab === 'global' ? styles.tabBtnActive : {}) }}
              onClick={() => setDebugTab('global')}
            >
              🌐 Глобальные
            </button>
          </div>
        </div>

        {debugTab === 'map' && (
          <DebugMapPage zoneState={zoneState} currentLocId={currentLocId} sendCommand={sendCommand} />
        )}

        {debugTab === 'characters' && renderCharactersDebug()}

        {debugTab === 'global' && renderGlobalDebug()}
      </div>
    );
  };

  // ─── render: characters debug tab ───────────────────────────────────────
  const renderCharactersDebug = () => {
    if (!zoneState) return null;
    const allAgents = Object.values(zoneState.agents);
    const locName = (id: string) => zoneState.locations[id]?.name ?? id;

    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ color: '#64748b', fontSize: '0.72rem', marginBottom: 4 }}>
          {allAgents.length} сталкеров в Зоне
        </div>
        {allAgents.map((agent) => (
          <div key={agent.id} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <AgentRow
                agent={agent as unknown as AgentForProfile}
                locationName={locName(agent.location_id)}
                locations={zoneState.locations}
                isCurrentPlayer={agent.id === myAgentId}
                sendCommand={sendCommand}
              />
            </div>
            <button
              style={{
                background: '#2d1515',
                border: '1px solid #7f1d1d',
                color: '#ef4444',
                borderRadius: 6,
                padding: '0.3rem 0.5rem',
                fontSize: '0.72rem',
                cursor: 'pointer',
                flexShrink: 0,
                lineHeight: 1,
              }}
              onClick={() => sendCommand('debug_delete_agent', { agent_id: agent.id })}
              title={`Удалить ${agent.name}`}
            >
              🗑
            </button>
          </div>
        ))}
      </div>
    );
  };

  // ─── render: global debug tab ───────────────────────────────────────────
  const renderGlobalDebug = () => {
    if (!zoneState) return null;
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16, padding: '8px 0' }}>
        {/* Time display */}
        <div style={{ color: '#94a3b8', fontSize: '0.82rem' }}>
          Время: День {zoneState.world_day} · {TIME_LABEL(zoneState.world_hour, zoneState.world_minute ?? 0)} · Ход {zoneState.world_turn}
        </div>

        {/* Global management actions */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ color: '#64748b', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 2 }}>
            Управление агентами
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            <button
              style={styles.btnWarning}
              onClick={() => sendCommand('debug_delete_all_npcs', {})}
              disabled={actionLoading}
              title="Удалить всех НПЦ-сталкеров (ботов) с карты"
            >
              🗑 Удалить всех НПЦ
            </button>
            <button
              style={styles.btnWarning}
              onClick={() => sendCommand('debug_delete_all_mutants', {})}
              disabled={actionLoading}
              title="Удалить всех мутантов с карты"
            >
              🗑 Удалить мутантов
            </button>
            <button
              style={styles.btnWarning}
              onClick={() => sendCommand('debug_delete_all_traders', {})}
              disabled={actionLoading}
              title="Удалить всех торговцев с карты"
            >
              🗑 Удалить торговцев
            </button>
            <button
              style={styles.btnWarning}
              onClick={() => sendCommand('debug_delete_all_artifacts', {})}
              disabled={actionLoading}
              title="Удалить все артефакты со всех локаций"
            >
              🗑 Удалить артефакты
            </button>
          </div>
        </div>

        {/* Time control */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ color: '#64748b', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 2 }}>
            Управление временем
          </div>
          <DebugTimeControl
            worldDay={zoneState.world_day}
            worldHour={zoneState.world_hour}
            worldMinute={zoneState.world_minute ?? 0}
            onSetTime={(day, hour, minute) => sendCommand('debug_set_time', { day, hour, minute })}
            disabled={actionLoading}
          />
        </div>
      </div>
    );
  };

  // ─── render: events ──────────────────────────────────────────────────────
  const renderEvents = () => {
    const contextEvents = context
      ? [...events]
          .filter((e) => e.context_id === context.id)
          .sort((a, b) => b.sequence_no - a.sequence_no)
          .slice(0, 20)
      : [];
    if (contextEvents.length === 0) return <p style={styles.emptyText}>No events yet.</p>;
    return (
      <div style={styles.eventList}>
        {contextEvents.map((ev) => (
          <div key={ev.id} style={styles.eventEntry}>
            <span style={styles.evType}>{ev.event_type}</span>
            <span style={styles.evPayload}>
              {Object.entries(ev.payload)
                .slice(0, 3)
                .map(([k, v]) => `${k}: ${JSON.stringify(v)}`)
                .join(' · ')}
            </span>
          </div>
        ))}
      </div>
    );
  };

  // ─── main render ─────────────────────────────────────────────────────────
  return (
    <div style={styles.root}>
      <div style={styles.header}>
        <h2 style={styles.title}>
          ☢️ Zone Stalkers
          <span style={styles.matchIdBadge}>{match.id.slice(0, 8)}…</span>
        </h2>
        <div style={styles.headerRight}>
          <span style={{ ...styles.statusPill, background: isActive ? '#166534' : '#334155' }}>
            {match.status}
          </span>
          {isCreator && (
            <button style={styles.btnDangerSmall} onClick={handleDeleteMatch} disabled={actionLoading}>
              ✕ Close Room
            </button>
          )}
        </div>
      </div>
      {isWaiting && renderLobby()}
      {isActive && showEntryMenu && entryScreen === 'main' && renderEntryMenu()}
      {isActive && showEntryMenu && entryScreen === 'npc_select' && renderNpcSelect()}
      {isActive && !showEntryMenu && showDebug && renderDebugScreen()}
      {isActive && !showEntryMenu && !showDebug && showRoster && renderRoster()}
      {isActive && !showEntryMenu && !showDebug && !showRoster && renderZoneMap()}
      {!isWaiting && !isActive && <p style={styles.loadingText}>Match status: {match.status}</p>}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  root: { display: 'flex', flexDirection: 'column', gap: '1.25rem' },
  header: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  headerRight: { display: 'flex', alignItems: 'center', gap: 10 },
  title: { color: '#f8fafc', margin: 0, fontSize: '1.25rem', display: 'flex', alignItems: 'center', gap: 10 },
  matchIdBadge: { color: '#475569', fontSize: '0.75rem', fontWeight: 400 },
  statusPill: { padding: '0.2rem 0.6rem', borderRadius: 12, color: '#fff', fontSize: '0.75rem', fontWeight: 600, textTransform: 'uppercase' as const },
  debugHeader: { display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' as const },

  // lobby
  lobby: { background: '#1e293b', borderRadius: 12, padding: '2rem', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '1rem', maxWidth: 480 },
  lobbyEmoji: { fontSize: '3rem' },
  lobbyTitle: { color: '#f8fafc', margin: 0, fontSize: '1.1rem' },
  lobbyHint: { color: '#94a3b8', margin: 0 },
  lobbyNote: { color: '#64748b', fontSize: '0.82rem', textAlign: 'center' as const, margin: 0 },
  participantList: { display: 'flex', gap: 8, flexWrap: 'wrap' as const },
  participantBadge: { background: '#334155', color: '#cbd5e1', borderRadius: 8, padding: '0.3rem 0.7rem', fontSize: '0.85rem' },
  lobbyActions: { display: 'flex', gap: 10, flexWrap: 'wrap' as const, justifyContent: 'center' as const },

  btnPrimary: { padding: '0.5rem 1.2rem', background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 8, cursor: 'pointer', fontWeight: 600, fontSize: '0.95rem' },
  btnDisabled: { background: '#334155', color: '#64748b', cursor: 'not-allowed' as const },
  btnDanger: { padding: '0.5rem 1.2rem', background: '#7f1d1d', color: '#fca5a5', border: '1px solid #ef4444', borderRadius: 8, cursor: 'pointer', fontWeight: 600 },
  btnDangerSmall: { padding: '0.25rem 0.7rem', background: '#7f1d1d', color: '#fca5a5', border: '1px solid #ef4444', borderRadius: 6, cursor: 'pointer', fontWeight: 600, fontSize: '0.78rem' },
  btnWarning: { padding: '0.35rem 0.9rem', background: '#451a03', color: '#fbbf24', border: '1px solid #b45309', borderRadius: 8, cursor: 'pointer', fontWeight: 600, fontSize: '0.85rem' },
  cancelBtn: { padding: '0.25rem 0.6rem', background: '#1e293b', color: '#94a3b8', border: '1px solid #334155', borderRadius: 6, cursor: 'pointer', fontSize: '0.8rem', marginTop: 4 },

  // map layout
  mapContainer: { display: 'flex', gap: '1rem', alignItems: 'flex-start', flexWrap: 'wrap' as const },

  agentPanel: {
    width: 200, flexShrink: 0,
    background: '#1e293b', borderRadius: 10, padding: '0.9rem',
    display: 'flex', flexDirection: 'column', gap: '0.4rem',
    border: '1px solid #334155',
  },
  panelTitle: { color: '#94a3b8', fontSize: '0.72rem', textTransform: 'uppercase' as const, letterSpacing: '0.06em', marginBottom: 2, fontWeight: 700 },
  agentName: { color: '#f8fafc', fontWeight: 700, fontSize: '1rem' },
  agentFaction: { color: '#64748b', fontSize: '0.78rem' },
  statRow: { display: 'flex', alignItems: 'center', gap: 6 },
  statLabel: { color: '#94a3b8', fontSize: '0.72rem', width: 32, flexShrink: 0 },
  barBg: { flex: 1, height: 6, background: '#0f172a', borderRadius: 3, overflow: 'hidden' },
  barFill: { height: '100%', borderRadius: 3, transition: 'width 0.3s' },
  statVal: { color: '#94a3b8', fontSize: '0.7rem', width: 36, textAlign: 'right' as const },
  moneyRow: { color: '#fbbf24', fontSize: '0.85rem', fontWeight: 600, marginTop: 4 },
  locationLabel: { color: '#60a5fa', fontSize: '0.8rem' },
  turnInfo: { color: '#475569', fontSize: '0.7rem', display: 'flex', alignItems: 'center', gap: 5, flexWrap: 'wrap' as const },
  actionUsedBadge: { background: '#334155', color: '#94a3b8', borderRadius: 6, padding: '0 5px', fontSize: '0.66rem' },
  inventoryTitle: { color: '#94a3b8', fontSize: '0.75rem', marginTop: 6 },
  inventoryList: { display: 'flex', flexDirection: 'column', gap: 3, maxHeight: 120, overflowY: 'auto' as const },
  inventoryItem: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: '#0f172a', borderRadius: 5, padding: '0.2rem 0.4rem' },
  itemName: { color: '#cbd5e1', fontSize: '0.72rem', flex: 1 },
  itemVal: { color: '#64748b', fontSize: '0.7rem', marginRight: 4 },
  useItemBtn: { padding: '0.1rem 0.4rem', background: '#166534', color: '#86efac', border: '1px solid #22c55e', borderRadius: 4, cursor: 'pointer', fontSize: '0.65rem', flexShrink: 0 },

  // center panel
  centerPanel: { flex: 1, minWidth: 280, display: 'flex', flexDirection: 'column', gap: '0.75rem' },
  centerPanelFull: { flex: 1, minWidth: 280, display: 'flex', flexDirection: 'column', gap: '0.75rem' },
  tabBar: { display: 'flex', gap: 6, flexWrap: 'wrap' as const },
  tabBtn: { padding: '0.35rem 0.8rem', background: '#1e293b', color: '#64748b', border: '1px solid #334155', borderRadius: 8, cursor: 'pointer', fontSize: '0.8rem', fontWeight: 600 },
  tabBtnActive: { background: '#0f172a', color: '#f8fafc', borderColor: '#475569' },
  debugTabBtn: { color: '#f59e0b', borderColor: '#78350f' },

  mapHint: { color: '#64748b', fontSize: '0.8rem', margin: 0 },
  locationGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))', gap: 8 },
  locationCard: {
    background: '#1e293b', borderRadius: 8, padding: '0.65rem 0.7rem',
    border: '1px solid #334155', borderLeft: '4px solid #334155',
    transition: 'border-color 0.15s, background 0.1s',
  },
  locationCurrent: { background: '#1e3a2a', border: '1px solid #22c55e', borderLeft: '4px solid #22c55e' },
  locationReachable: { border: '1px solid #3b82f6' },
  locationSelected: { background: '#1e3a5f' },
  locRow: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 4 },
  locName: { color: '#f8fafc', fontWeight: 600, fontSize: '0.85rem' },
  dangerBadge: { padding: '0.1rem 0.4rem', borderRadius: 8, color: '#fff', fontSize: '0.68rem', fontWeight: 700, flexShrink: 0 },
  locType: { color: '#64748b', fontSize: '0.72rem', marginTop: 2 },
  locIcons: { display: 'flex', gap: 4, flexWrap: 'wrap' as const, marginTop: 4 },
  locBadge: { background: '#334155', color: '#94a3b8', borderRadius: 6, padding: '0.1rem 0.4rem', fontSize: '0.68rem' },
  locBadgeSelf: { background: '#166534', color: '#86efac', borderRadius: 6, padding: '0.1rem 0.4rem', fontSize: '0.68rem', fontWeight: 700 },
  locBadgeArt: { background: '#312e81', color: '#a5b4fc', borderRadius: 6, padding: '0.1rem 0.4rem', fontSize: '0.68rem' },
  locBadgeAnom: { background: '#4a044e', color: '#e879f9', borderRadius: 6, padding: '0.1rem 0.4rem', fontSize: '0.68rem' },
  moveHint: { color: '#60a5fa', fontSize: '0.7rem', marginTop: 4, fontStyle: 'italic' },

  locDetail: { background: '#1e293b', borderRadius: 8, padding: '0.75rem', border: '1px solid #334155' },
  locDetailTitle: { color: '#f8fafc', fontWeight: 700, fontSize: '0.9rem', marginBottom: 8 },
  locDetailSection: { marginBottom: 8 },
  locDetailLabel: { color: '#94a3b8', fontSize: '0.72rem', textTransform: 'uppercase' as const, marginBottom: 4, fontWeight: 700 },
  locDetailItem: { display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.82rem', color: '#cbd5e1', marginBottom: 3 },
  pickUpBtn: { marginLeft: 'auto', padding: '0.15rem 0.55rem', background: '#1e3a5f', color: '#60a5fa', border: '1px solid #3b82f6', borderRadius: 5, cursor: 'pointer', fontSize: '0.72rem' },

  // event panel
  eventPanel: { display: 'flex', flexDirection: 'column', gap: '0.75rem' },
  eventTitle: { color: '#f8fafc', fontWeight: 700, fontSize: '1.05rem' },
  eventPhase: { fontSize: '0.8rem', fontWeight: 600 },
  narrationHistory: { display: 'flex', flexDirection: 'column', gap: 10, maxHeight: 280, overflowY: 'auto' as const },
  narrationEntry: { background: '#0f172a', borderRadius: 8, padding: '0.75rem', border: '1px solid #334155' },
  narrationTurn: { color: '#475569', fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase' as const, marginBottom: 4 },
  narrationText: { color: '#cbd5e1', fontSize: '0.88rem', lineHeight: 1.6 },
  choicesSummary: { display: 'flex', gap: 6, flexWrap: 'wrap' as const, marginTop: 8 },
  choiceBadge: { background: '#1e3a5f', color: '#60a5fa', borderRadius: 8, padding: '0.15rem 0.5rem', fontSize: '0.7rem' },
  optionsPanel: { display: 'flex', flexDirection: 'column', gap: 8 },
  optionsLabel: { color: '#94a3b8', fontSize: '0.8rem', fontWeight: 600 },
  optionBtn: { padding: '0.6rem 1rem', background: '#1e293b', color: '#f8fafc', border: '1px solid #334155', borderRadius: 8, cursor: 'pointer', fontSize: '0.9rem', textAlign: 'left' as const },
  optionBtnChosen: { background: '#166534', borderColor: '#22c55e', color: '#86efac' },
  outcomeBox: { background: '#1e293b', borderRadius: 8, padding: '0.75rem', border: '1px solid #475569' },
  outcomeLabel: { color: '#94a3b8', fontSize: '0.72rem', fontWeight: 700, marginBottom: 6 },
  outcomeText: { color: '#cbd5e1', fontSize: '0.9rem', margin: 0, lineHeight: 1.6 },
  leaveEventBtn: { padding: '0.3rem 0.8rem', background: 'transparent', color: '#64748b', border: '1px solid #334155', borderRadius: 6, cursor: 'pointer', fontSize: '0.8rem', alignSelf: 'flex-start' as const },

  // memory panel
  memoryPanel: { display: 'flex', flexDirection: 'column', gap: '0.6rem' },
  memoryList: { display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 480, overflowY: 'auto' as const },
  memoryEntry: { background: '#1e293b', borderRadius: 8, padding: '0.65rem', border: '1px solid #334155' },
  memoryHeader: { display: 'flex', justifyContent: 'space-between', marginBottom: 4 },
  memoryType: { color: '#94a3b8', fontSize: '0.72rem', fontWeight: 700 },
  memoryWhen: { color: '#475569', fontSize: '0.7rem' },
  memoryTitle: { color: '#f8fafc', fontWeight: 600, fontSize: '0.85rem', marginBottom: 4 },
  memorySummary: { color: '#94a3b8', fontSize: '0.8rem', lineHeight: 1.5 },
  memoryEffects: { display: 'flex', gap: 6, flexWrap: 'wrap' as const, marginTop: 6 },
  effectChip: { background: '#0f172a', borderRadius: 6, padding: '0.1rem 0.45rem', fontSize: '0.7rem' },

  // right panel
  rightPanel: { width: 220, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: '1rem' },

  // action panel
  actionPanel: { background: '#1e293b', borderRadius: 10, padding: '0.9rem', display: 'flex', flexDirection: 'column', gap: '0.5rem', border: '1px solid #334155' },
  actionBtn: { padding: '0.4rem 0.8rem', background: '#0f172a', color: '#cbd5e1', border: '1px solid #334155', borderRadius: 7, cursor: 'pointer', fontSize: '0.82rem', textAlign: 'left' as const },
  endTurnBtn: { padding: '0.45rem 1rem', background: '#334155', color: '#f8fafc', border: 'none', borderRadius: 8, cursor: 'pointer', fontWeight: 600, fontSize: '0.875rem', marginTop: 4 },
  tickBtn: { padding: '0.3rem 0.8rem', background: 'transparent', color: '#475569', border: '1px dashed #334155', borderRadius: 6, cursor: 'pointer', fontSize: '0.75rem' },

  schedCard: { background: '#0f172a', borderRadius: 8, padding: '0.6rem', border: '1px solid #1e3a5f', display: 'flex', flexDirection: 'column', gap: 6 },
  schedRow: { display: 'flex', alignItems: 'center', gap: 6 },
  schedIcon: { fontSize: '1.2rem', flexShrink: 0 },
  schedLabel: { color: '#cbd5e1', fontSize: '0.78rem', flex: 1, lineHeight: 1.3 },
  schedTurns: { color: '#60a5fa', fontSize: '0.72rem', fontWeight: 700, flexShrink: 0 },
  progressBg: { height: 4, background: '#334155', borderRadius: 2, overflow: 'hidden' },
  progressFill: { height: '100%', background: '#3b82f6', borderRadius: 2, transition: 'width 0.3s' },
  schedHint: { color: '#475569', fontSize: '0.68rem', margin: 0 },

  sleepRow: { display: 'flex', gap: 6, alignItems: 'center' },
  sleepSelect: { padding: '0.35rem 0.4rem', background: '#0f172a', color: '#cbd5e1', border: '1px solid #334155', borderRadius: 6, fontSize: '0.8rem' },

  travelPanel: { background: '#0f172a', borderRadius: 8, padding: '0.6rem', display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 200, overflowY: 'auto' as const },
  travelLocBtn: { padding: '0.3rem 0.6rem', background: '#1e293b', color: '#94a3b8', border: '1px solid #334155', borderRadius: 6, cursor: 'pointer', fontSize: '0.78rem', textAlign: 'left' as const },
  travelLocBtnSelected: { background: '#1e3a5f', color: '#f8fafc', borderColor: '#3b82f6' },
  travelDanger: { color: '#f59e0b', fontSize: '0.7rem' },

  eventsPanel: { background: '#1e293b', borderRadius: 10, padding: '0.9rem', display: 'flex', flexDirection: 'column', gap: '0.5rem', border: '1px solid #334155', flex: 1 },
  eventList: { display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 280, overflowY: 'auto' as const },
  eventEntry: { background: '#0f172a', borderRadius: 6, padding: '0.3rem 0.5rem', display: 'flex', flexDirection: 'column', gap: 2 },
  evType: { color: '#a78bfa', fontWeight: 600, fontSize: '0.73rem' },
  evPayload: { color: '#475569', fontSize: '0.67rem' },

  emptyText: { color: '#475569', fontSize: '0.8rem' },
  loadingText: { color: '#94a3b8' },
  error: { color: '#f87171', fontSize: '0.85rem', margin: 0 },

  // ── Roster page ───────────────────────────────────────────────────────────
  rosterPage: { display: 'flex', flexDirection: 'column' as const, gap: '1.5rem' },
  rosterHeader: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' as const },
  rosterTitle: { color: '#f8fafc', fontSize: '1.1rem', margin: 0 },
  rosterSubtitle: { color: '#64748b', fontSize: '0.8rem', margin: '2px 0 0' },
  rosterEnterBtn: { padding: '0.55rem 1.4rem', background: '#166534', color: '#86efac', border: '1px solid #22c55e', borderRadius: 10, cursor: 'pointer', fontWeight: 700, fontSize: '0.95rem' },
  rosterSection: { display: 'flex', flexDirection: 'column' as const, gap: 10 },
  rosterSectionTitle: { color: '#94a3b8', fontSize: '0.75rem', fontWeight: 700, textTransform: 'uppercase' as const, letterSpacing: '0.06em' },
  rosterGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 10 },
  rosterCard: { background: '#1e293b', borderRadius: 10, padding: '0.85rem', border: '1px solid #334155', display: 'flex', flexDirection: 'column' as const, gap: '0.5rem' },
  rosterCardMe: { border: '1px solid #22c55e', background: '#0d2a1a' },
  rosterCardTop: { display: 'flex', alignItems: 'flex-start', gap: 8 },
  rosterCardAvatar: { fontSize: '1.5rem', flexShrink: 0 },
  rosterCardInfo: { flex: 1 },
  rosterCardName: { color: '#f8fafc', fontWeight: 700, fontSize: '0.9rem' },
  rosterCardSub: { color: '#64748b', fontSize: '0.72rem', marginTop: 1 },
  rosterAliveTag: { borderRadius: 6, padding: '0.1rem 0.4rem', fontSize: '0.68rem', fontWeight: 700, flexShrink: 0 },
  rosterStatRow: { display: 'flex', alignItems: 'center', gap: 5 },
  rosterStatLabel: { color: '#94a3b8', fontSize: '0.68rem', width: 18, flexShrink: 0 },
  rosterStatVal: { color: '#64748b', fontSize: '0.68rem', width: 28, textAlign: 'right' as const },
  rosterLocation: { color: '#60a5fa', fontSize: '0.75rem' },
  rosterSched: { color: '#a78bfa', fontSize: '0.7rem', fontStyle: 'italic' },
  rosterMoney: { color: '#fbbf24', fontSize: '0.78rem', fontWeight: 600 },
  rosterCardFooter: { display: 'flex', gap: 6, marginTop: 2, flexWrap: 'wrap' as const, alignItems: 'center' },
  rosterViewBtn: { padding: '0.2rem 0.65rem', background: '#0f172a', color: '#94a3b8', border: '1px solid #334155', borderRadius: 6, cursor: 'pointer', fontSize: '0.72rem' },
  rosterPlayBtn: { padding: '0.2rem 0.65rem', background: '#166534', color: '#86efac', border: '1px solid #22c55e', borderRadius: 6, cursor: 'pointer', fontSize: '0.72rem', fontWeight: 700 },
  rosterTabBtn: { color: '#a78bfa', borderColor: '#312e81' },

  // ── Agent profile overlay ─────────────────────────────────────────────────
  profileOverlay: { position: 'fixed' as const, inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1000, display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '1.5rem', overflowY: 'auto' as const },
  profileModal: { background: '#0f172a', borderRadius: 14, border: '1px solid #334155', padding: '1.5rem', width: '100%', maxWidth: 560, display: 'flex', flexDirection: 'column' as const, gap: '1rem' },
  profileHeader: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, flexWrap: 'wrap' as const },
  profileBackBtn: { padding: '0.3rem 0.8rem', background: 'transparent', color: '#94a3b8', border: '1px solid #334155', borderRadius: 7, cursor: 'pointer', fontSize: '0.8rem' },
  profileEnterBtn: { padding: '0.4rem 1rem', background: '#166534', color: '#86efac', border: '1px solid #22c55e', borderRadius: 8, cursor: 'pointer', fontWeight: 700, fontSize: '0.9rem' },
  profileTitle: { display: 'flex', gap: 12, alignItems: 'flex-start' },
  profileAvatar: { fontSize: '2.5rem', flexShrink: 0 },
  profileName: { color: '#f8fafc', fontWeight: 700, fontSize: '1.15rem' },
  profileSubtitle: { color: '#64748b', fontSize: '0.8rem', marginTop: 2 },
  profileSection: { background: '#1e293b', borderRadius: 8, padding: '0.75rem', border: '1px solid #334155', display: 'flex', flexDirection: 'column' as const, gap: '0.45rem' },
  profileSectionLabel: { color: '#94a3b8', fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase' as const, letterSpacing: '0.06em', marginBottom: 2 },
  profileSectionVal: { color: '#cbd5e1', fontSize: '0.9rem' },
  profileSched: { color: '#a78bfa', fontSize: '0.8rem', fontStyle: 'italic' },
  profileStatRow: { display: 'flex', alignItems: 'center', gap: 8 },
  profileStatLabel: { color: '#94a3b8', fontSize: '0.72rem', width: 70, flexShrink: 0 },
  profileStatVal: { color: '#94a3b8', fontSize: '0.7rem', width: 42, textAlign: 'right' as const },
  profileMoney: { color: '#fbbf24', fontWeight: 600, fontSize: '0.9rem', marginTop: 4 },
  profileRep: { color: '#a78bfa', fontSize: '0.8rem' },
  profileSkillGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))', gap: 6 },
  profileSkillChip: { background: '#0f172a', borderRadius: 6, padding: '0.3rem 0.5rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' },
  profileSkillLabel: { color: '#94a3b8', fontSize: '0.72rem' },
  profileSkillVal: { color: '#f8fafc', fontWeight: 700, fontSize: '0.75rem' },
  profileGoal: { color: '#94a3b8', fontSize: '0.8rem', marginTop: 4 },
  profileSubgoal: { color: '#60a5fa', fontSize: '0.78rem' },
  profileEquipRow: { display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.82rem' },
  profileEquipSlot: { color: '#64748b', fontSize: '0.7rem', width: 56, flexShrink: 0, textTransform: 'capitalize' as const },
  profileEquipItem: { color: '#cbd5e1', flex: 1 },
  profileEquipEmpty: { color: '#334155', flex: 1 },
  profileInvRow: { display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.82rem', borderBottom: '1px solid #0f172a', paddingBottom: 3 },
  profileInvWeight: { color: '#475569', fontSize: '0.7rem' },
  profileMemoryList: { display: 'flex', flexDirection: 'column' as const, gap: 6, maxHeight: 260, overflowY: 'auto' as const },

  // ── Entry menu ────────────────────────────────────────────────────────────
  entryPage: {
    position: 'relative' as const,
    minHeight: 480,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '2rem 1rem',
  },
  entryBg: {
    position: 'absolute' as const,
    inset: 0,
    background: 'radial-gradient(ellipse at 50% 30%, rgba(34,197,94,0.05) 0%, transparent 70%)',
    pointerEvents: 'none' as const,
  },
  entryCard: {
    position: 'relative' as const,
    background: '#0a1020',
    border: '1px solid #1e293b',
    borderRadius: 16,
    padding: '2.5rem 2rem',
    maxWidth: 440,
    width: '100%',
    display: 'flex',
    flexDirection: 'column' as const,
    alignItems: 'center',
    gap: '0.85rem',
    boxShadow: '0 0 40px rgba(34,197,94,0.06)',
  },
  entryLogo: { fontSize: '3.5rem', lineHeight: 1 },
  entryTitle: {
    color: '#f8fafc',
    fontSize: '1.6rem',
    fontWeight: 900,
    letterSpacing: '0.12em',
    margin: 0,
    textShadow: '0 0 20px rgba(34,197,94,0.3)',
  },
  entrySubtitle: {
    color: '#475569',
    fontSize: '0.78rem',
    letterSpacing: '0.04em',
  },
  entrySep: { width: '100%', height: 1, background: '#1e293b', margin: '0.5rem 0' },
  entryBtn: {
    width: '100%',
    display: 'flex',
    alignItems: 'center',
    gap: 14,
    background: '#0f172a',
    border: '1px solid #1e293b',
    borderRadius: 10,
    padding: '0.85rem 1.1rem',
    cursor: 'pointer',
    transition: 'border-color 0.15s, background 0.1s',
    textAlign: 'left' as const,
  },
  entryBtnPrimary: {
    border: '1px solid #166534',
    background: '#0d1f15',
  },
  entryBtnDebug: {
    border: '1px solid #78350f',
    background: '#160d02',
  },
  entryBtnIcon: { fontSize: '1.6rem', flexShrink: 0, lineHeight: 1 },
  entryBtnText: { display: 'flex', flexDirection: 'column' as const, gap: 2 },
  entryBtnLabel: { color: '#f8fafc', fontWeight: 700, fontSize: '0.95rem' },
  entryBtnHint: { color: '#475569', fontSize: '0.72rem' },

  // ── NPC select ────────────────────────────────────────────────────────────
  npcSelectPage: { display: 'flex', flexDirection: 'column' as const, gap: '1.25rem' },
  npcSelectHeader: { display: 'flex', flexDirection: 'column' as const, gap: 6 },
  npcBackBtn: {
    alignSelf: 'flex-start' as const,
    padding: '0.3rem 0.75rem',
    background: 'transparent',
    color: '#64748b',
    border: '1px solid #334155',
    borderRadius: 7,
    cursor: 'pointer',
    fontSize: '0.8rem',
  },
  npcSelectTitle: { color: '#f8fafc', fontSize: '1.05rem', margin: 0 },
  npcSelectHint: { color: '#64748b', fontSize: '0.8rem' },
  npcGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 10 },
  npcEmpty: { color: '#64748b', background: '#1e293b', borderRadius: 10, padding: '1.5rem', textAlign: 'center' as const },
  npcCard: {
    background: '#1e293b',
    borderRadius: 10,
    padding: '0.85rem',
    border: '1px solid #334155',
    display: 'flex',
    flexDirection: 'column' as const,
    gap: '0.45rem',
  },
  npcCardTop: { display: 'flex', alignItems: 'flex-start', gap: 8 },
  npcAvatar: { fontSize: '1.4rem', flexShrink: 0 },
  npcInfo: { flex: 1 },
  npcName: { color: '#f8fafc', fontWeight: 700, fontSize: '0.9rem' },
  npcSub: { color: '#64748b', fontSize: '0.72rem', marginTop: 1 },
  npcAliveTag: { borderRadius: 6, padding: '0.1rem 0.45rem', fontSize: '0.68rem', fontWeight: 700, flexShrink: 0 },
  npcLoc: { color: '#60a5fa', fontSize: '0.75rem' },
  npcSched: { color: '#a78bfa', fontSize: '0.7rem', fontStyle: 'italic' },
  npcStats: { display: 'flex', gap: 10, color: '#fbbf24', fontSize: '0.78rem', fontWeight: 600 },
  npcCardFooter: { display: 'flex', gap: 6, marginTop: 4, alignItems: 'center' },
  npcViewBtn: {
    padding: '0.2rem 0.6rem',
    background: '#0f172a',
    color: '#94a3b8',
    border: '1px solid #334155',
    borderRadius: 6,
    cursor: 'pointer',
    fontSize: '0.72rem',
  },
  npcTakeBtn: {
    flex: 1,
    padding: '0.3rem 0.7rem',
    background: '#166534',
    color: '#86efac',
    border: '1px solid #22c55e',
    borderRadius: 7,
    cursor: 'pointer',
    fontWeight: 700,
    fontSize: '0.78rem',
  },
};

