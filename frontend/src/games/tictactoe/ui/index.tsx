import React, { useCallback, useEffect, useRef, useState } from 'react';
import { commandsApi, contextsApi, eventsApi, matchesApi } from '../../../api/client';
import type {
  GameContext,
  GameEvent,
  Match,
  MatchParticipant,
  TicTacToeState,
  User,
} from '../../../types';

interface Props {
  match: Match;
  user: User;
  onMatchUpdated: (match: Match) => void;
  onMatchDeleted: (matchId: string) => void;
}

const MARK_COLORS: Record<string, string> = { X: '#60a5fa', O: '#f87171' };

export default function TicTacToeGame({ match, user, onMatchUpdated, onMatchDeleted }: Props) {
  const [context, setContext] = useState<GameContext | null>(null);
  const [events, setEvents] = useState<GameEvent[]>([]);
  const [participants, setParticipants] = useState<MatchParticipant[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const lobbyPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const ttt: TicTacToeState | null = context
    ? (context.state_blob as unknown as TicTacToeState)
    : null;

  const myMark = ttt?.player_marks?.[user.id] ?? null;
  const isMyTurn = !ttt?.game_over && ttt?.current_player_id === user.id;
  const isCreator = match.created_by_user_id === user.id;
  const isWaiting =
    match.status === 'waiting_for_players' || match.status === 'draft';
  const isActive = match.status === 'active';

  // ─── load participants ────────────────────────────────────────────────────
  const loadParticipants = useCallback(async () => {
    try {
      const res = await matchesApi.participants(match.id);
      setParticipants(res.data as MatchParticipant[]);
    } catch {
      // non-fatal
    }
  }, [match.id]);

  // ─── load / refresh context and events ───────────────────────────────────
  const refresh = useCallback(async () => {
    try {
      const ctxRes = await contextsApi.getTree(match.id);
      const ctxList = ctxRes.data as GameContext[];
      const tttCtx = ctxList.find((c) => c.context_type === 'tictactoe') ?? null;
      setContext(tttCtx);

      const evRes = await eventsApi.listForMatch(match.id);
      setEvents(evRes.data as GameEvent[]);
    } catch {
      // ignore transient errors
    }
  }, [match.id]);

  // ─── ensure context exists when match is active ───────────────────────────
  const ensureContext = useCallback(async () => {
    const ctxRes = await contextsApi.getTree(match.id);
    const ctxList = ctxRes.data as GameContext[];
    const existing = ctxList.find((c) => c.context_type === 'tictactoe');
    if (existing) {
      setContext(existing);
      return existing;
    }
    const newCtx = await contextsApi.create({
      match_id: match.id,
      context_type: 'tictactoe',
    });
    setContext(newCtx.data as GameContext);
    return newCtx.data as GameContext;
  }, [match.id]);

  // ─── initial load ─────────────────────────────────────────────────────────
  useEffect(() => {
    loadParticipants();
    if (isActive) {
      ensureContext().then(() => refresh());
    } else {
      refresh();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [match.id, match.status]);

  // ─── lobby polling — updates participants + detects game start ────────────
  useEffect(() => {
    if (lobbyPollRef.current) clearInterval(lobbyPollRef.current);

    if (!isWaiting) return;

    lobbyPollRef.current = setInterval(async () => {
      await loadParticipants();
      try {
        const mRes = await matchesApi.get(match.id);
        const updated = mRes.data as Match;
        if (updated.status === 'archived') {
          onMatchDeleted(match.id);
          return;
        }
        if (updated.status !== match.status) {
          onMatchUpdated(updated);
        }
      } catch (e: unknown) {
        // 404 means match was deleted by the creator
        if ((e as { response?: { status?: number } })?.response?.status === 404) {
          onMatchDeleted(match.id);
        }
      }
    }, 2500);

    return () => {
      if (lobbyPollRef.current) clearInterval(lobbyPollRef.current);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isWaiting, match.id, match.status, loadParticipants, onMatchUpdated, onMatchDeleted]);

  // ─── active-game polling ──────────────────────────────────────────────────
  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);

    if (isActive && !ttt?.game_over) {
      pollRef.current = setInterval(async () => {
        try {
          const mRes = await matchesApi.get(match.id);
          const updated = mRes.data as Match;
          if (updated.status === 'archived') {
            onMatchDeleted(match.id);
            return;
          }
          if (updated.status !== match.status) {
            onMatchUpdated(updated);
          }
        } catch (e: unknown) {
          if ((e as { response?: { status?: number } })?.response?.status === 404) {
            onMatchDeleted(match.id);
          }
        }
        await refresh();
        await loadParticipants();
      }, 2500);
    }

    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isActive, ttt?.game_over, match.id, refresh, loadParticipants, onMatchUpdated, onMatchDeleted]);

  // ─── actions ──────────────────────────────────────────────────────────────
  const handleJoin = async () => {
    setActionLoading(true);
    setError(null);
    try {
      await matchesApi.join(match.id);
      await loadParticipants();
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      if (msg && !msg.toLowerCase().includes('already')) {
        setError(msg);
      }
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
    if (!window.confirm('Close this room? This cannot be undone.')) return;
    setActionLoading(true);
    setError(null);
    try {
      await matchesApi.delete(match.id);
      onMatchDeleted(match.id);
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(msg ?? 'Failed to close room.');
      setActionLoading(false);
    }
  };

  const handlePlaceMark = async (cell: number) => {
    if (!context || !isMyTurn || ttt?.game_over) return;
    setActionLoading(true);
    setError(null);
    try {
      const res = await commandsApi.submit({
        match_id: match.id,
        context_id: context.id,
        command_type: 'place_mark',
        payload: { cell },
      });
      if (res.data.status === 'rejected') {
        setError(res.data.error ?? 'Move rejected');
      }
      await refresh();
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(msg ?? 'Command failed.');
    } finally {
      setActionLoading(false);
    }
  };

  const alreadyJoined = participants.some((p) => p.user_id === user.id);

  // ─── render helpers ───────────────────────────────────────────────────────
  const renderLobby = () => {
    const canStart = isCreator && participants.length >= 2;
    return (
      <div style={styles.lobby}>
        <h3 style={styles.lobbyTitle}>Tic-Tac-Toe — Waiting for players</h3>
        <p style={styles.lobbyHint}>
          {participants.length} / 2 player{participants.length !== 1 ? 's' : ''} joined
        </p>
        <div style={styles.participantList}>
          {participants.map((p) => (
            <div key={p.id} style={styles.participantBadge}>
              👤 {p.display_name ?? p.user_id?.slice(0, 8) ?? 'Unknown'}
            </div>
          ))}
        </div>
        <div style={styles.lobbyActions}>
          {!alreadyJoined && (
            <button style={styles.btnPrimary} onClick={handleJoin} disabled={actionLoading}>
              {actionLoading ? '…' : 'Join Game'}
            </button>
          )}
          {isCreator && (
            <button
              style={{ ...styles.btnPrimary, ...(canStart ? {} : styles.btnDisabled) }}
              onClick={handleStart}
              disabled={actionLoading || !canStart}
              title={canStart ? 'Start the game' : 'Need at least 2 players'}
            >
              {actionLoading ? '…' : 'Start Game'}
            </button>
          )}
          {isCreator && (
            <button
              style={styles.btnDanger}
              onClick={handleDeleteMatch}
              disabled={actionLoading}
              title="Close and delete this room"
            >
              {actionLoading ? '…' : 'Close Room'}
            </button>
          )}
        </div>
        {error && <p style={styles.error}>{error}</p>}
      </div>
    );
  };

  const renderBoard = () => {
    if (!ttt) return <p style={styles.loadingText}>Setting up board…</p>;

    const board = ttt.board ?? Array(9).fill(null);
    const gameOver = ttt.game_over;

    let statusLine: React.ReactNode;
    if (gameOver) {
      if (ttt.winner) {
        const youWin = ttt.winner === user.id;
        statusLine = (
          <div style={{ ...styles.statusBanner, background: youWin ? '#166534' : '#7f1d1d' }}>
            {youWin ? '🎉 You win!' : `❌ You lose. Winner: ${ttt.winner_mark}`}
          </div>
        );
      } else {
        statusLine = <div style={{ ...styles.statusBanner, background: '#1e3a5f' }}>🤝 Draw!</div>;
      }
    } else if (isMyTurn) {
      statusLine = (
        <div style={{ ...styles.statusBanner, background: '#1a3a2a' }}>
          ✅ Your turn! You are&nbsp;
          <span style={{ color: MARK_COLORS[myMark ?? 'X'], fontWeight: 700 }}>
            {myMark ?? '?'}
          </span>
        </div>
      );
    } else {
      statusLine = (
        <div style={{ ...styles.statusBanner, background: '#1e293b' }}>
          ⏳ Waiting for opponent…{' '}
          {myMark && (
            <span>
              You are&nbsp;
              <span style={{ color: MARK_COLORS[myMark], fontWeight: 700 }}>{myMark}</span>
            </span>
          )}
        </div>
      );
    }

    return (
      <div style={styles.gameArea}>
        {statusLine}
        <div style={styles.board}>
          {board.map((cell, idx) => {
            const isEmpty = cell === null;
            const canClick = isEmpty && isMyTurn && !gameOver && !actionLoading;
            return (
              <button
                key={idx}
                style={{
                  ...styles.cell,
                  cursor: canClick ? 'pointer' : 'default',
                  color: cell ? MARK_COLORS[cell as string] : 'transparent',
                  background: canClick ? '#1e293b' : '#0f172a',
                }}
                onClick={() => canClick && handlePlaceMark(idx)}
                disabled={!canClick}
              >
                {cell ?? ''}
              </button>
            );
          })}
        </div>
        {error && <p style={styles.error}>{error}</p>}
      </div>
    );
  };

  const renderEvents = () => {
    const contextEvents = context
      ? [...events]
          .filter((e) => e.context_id === context.id)
          .sort((a, b) => a.sequence_no - b.sequence_no)
          .slice(-20)
      : [];
    if (contextEvents.length === 0)
      return <p style={styles.noEvents}>No events yet.</p>;
    return (
      <div style={styles.eventList}>
        {contextEvents.map((ev) => (
          <div key={ev.id} style={styles.eventEntry}>
            <span style={styles.evType}>{ev.event_type}</span>
            <span style={styles.evPayload}>
              {Object.entries(ev.payload)
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
          Tic-Tac-Toe
          <span style={styles.matchIdBadge}>{match.id.slice(0, 8)}…</span>
        </h2>
        <div style={styles.headerRight}>
          <span
            style={{
              ...styles.statusPill,
              background: isActive ? '#166534' : '#334155',
            }}
          >
            {match.status}
          </span>
          {isCreator && !isWaiting && (
            <button
              style={styles.btnDangerSmall}
              onClick={handleDeleteMatch}
              disabled={actionLoading}
              title="Close and delete this match"
            >
              ✕ Close Room
            </button>
          )}
        </div>
      </div>

      {isWaiting && renderLobby()}

      {isActive && (
        <div style={styles.twoCol}>
          <div>{renderBoard()}</div>
          <div style={styles.eventsPanel}>
            <h3 style={styles.eventsTitle}>Event Log</h3>
            {renderEvents()}
          </div>
        </div>
      )}

      {!isWaiting && !isActive && (
        <p style={styles.loadingText}>Match status: {match.status}</p>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  root: {
    display: 'flex',
    flexDirection: 'column',
    gap: '1.25rem',
    maxWidth: 900,
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
  },
  headerRight: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  title: {
    color: '#f8fafc',
    margin: 0,
    fontSize: '1.25rem',
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  matchIdBadge: {
    color: '#475569',
    fontSize: '0.75rem',
    fontWeight: 400,
  },
  statusPill: {
    padding: '0.2rem 0.6rem',
    borderRadius: 12,
    color: '#fff',
    fontSize: '0.75rem',
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
  },
  lobby: {
    background: '#1e293b',
    borderRadius: 12,
    padding: '2rem',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: '1rem',
    maxWidth: 420,
  },
  lobbyTitle: { color: '#f8fafc', margin: 0, fontSize: '1.1rem' },
  lobbyHint: { color: '#94a3b8', margin: 0 },
  participantList: { display: 'flex', gap: 8, flexWrap: 'wrap' },
  participantBadge: {
    background: '#334155',
    color: '#cbd5e1',
    borderRadius: 8,
    padding: '0.3rem 0.7rem',
    fontSize: '0.85rem',
  },
  lobbyActions: { display: 'flex', gap: 10, flexWrap: 'wrap', justifyContent: 'center' },
  btnPrimary: {
    padding: '0.5rem 1.2rem',
    background: '#3b82f6',
    color: '#fff',
    border: 'none',
    borderRadius: 8,
    cursor: 'pointer',
    fontWeight: 600,
    fontSize: '0.95rem',
  },
  btnDisabled: { background: '#334155', color: '#64748b', cursor: 'not-allowed' },
  btnDanger: {
    padding: '0.5rem 1.2rem',
    background: '#7f1d1d',
    color: '#fca5a5',
    border: '1px solid #ef4444',
    borderRadius: 8,
    cursor: 'pointer',
    fontWeight: 600,
    fontSize: '0.95rem',
  },
  btnDangerSmall: {
    padding: '0.25rem 0.7rem',
    background: '#7f1d1d',
    color: '#fca5a5',
    border: '1px solid #ef4444',
    borderRadius: 6,
    cursor: 'pointer',
    fontWeight: 600,
    fontSize: '0.78rem',
  },
  twoCol: {
    display: 'flex',
    gap: '2rem',
    alignItems: 'flex-start',
    flexWrap: 'wrap',
  },
  gameArea: {
    display: 'flex',
    flexDirection: 'column',
    gap: '1rem',
    alignItems: 'flex-start',
  },
  statusBanner: {
    padding: '0.6rem 1.2rem',
    borderRadius: 8,
    color: '#f8fafc',
    fontSize: '0.95rem',
    fontWeight: 500,
    minWidth: 280,
  },
  board: {
    display: 'grid',
    gridTemplateColumns: 'repeat(3, 1fr)',
    gap: 6,
  },
  cell: {
    width: 90,
    height: 90,
    fontSize: '2.6rem',
    fontWeight: 800,
    border: '2px solid #334155',
    borderRadius: 10,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    transition: 'background 0.12s',
    outline: 'none',
  },
  eventsPanel: {
    flex: 1,
    minWidth: 220,
  },
  eventsTitle: {
    color: '#94a3b8',
    fontSize: '0.8rem',
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
    margin: '0 0 0.5rem 0',
  },
  eventList: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
    maxHeight: 360,
    overflowY: 'auto',
  },
  eventEntry: {
    background: '#1e293b',
    borderRadius: 6,
    padding: '0.35rem 0.6rem',
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
  },
  evType: {
    color: '#a78bfa',
    fontWeight: 600,
    fontSize: '0.78rem',
  },
  evPayload: {
    color: '#64748b',
    fontSize: '0.7rem',
  },
  noEvents: {
    color: '#475569',
    fontSize: '0.85rem',
  },
  loadingText: { color: '#94a3b8' },
  error: { color: '#f87171', margin: '0.25rem 0', fontSize: '0.85rem' },
};

