import React, { useState } from 'react';
import { matchesApi } from '../../api/client';
import { useAppState } from '../../store';
import { GAME_CATALOG } from '../../games/registry';
import type { Match } from '../../types';

export default function GameLobby() {
  const { state, dispatch } = useAppState();
  const [creatingId, setCreatingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleCreate = async (gameId: string) => {
    setCreatingId(gameId);
    setError(null);
    try {
      const res = await matchesApi.create({ game_id: gameId });
      const newMatch = res.data as Match;
      dispatch({ type: 'SET_MATCHES', payload: [newMatch, ...state.matches] });
      dispatch({ type: 'SET_CURRENT_MATCH', payload: newMatch });
    } catch {
      setError(`Failed to create a ${gameId} match.`);
    } finally {
      setCreatingId(null);
    }
  };

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <h2 style={styles.title}>Game Catalog</h2>
        <p style={styles.subtitle}>Choose a game and create a new room.</p>
      </div>

      {error && <p style={styles.error}>{error}</p>}

      <div style={styles.grid}>
        {GAME_CATALOG.map((game) => (
          <div key={game.id} style={styles.card}>
            <div style={styles.cardTop}>
              <span style={styles.cardEmoji}>{game.emoji}</span>
              <div>
                <h3 style={styles.cardTitle}>{game.name}</h3>
                <div style={styles.tagRow}>
                  {game.tags.map((t) => (
                    <span key={t} style={styles.tag}>{t}</span>
                  ))}
                </div>
              </div>
            </div>
            <p style={styles.cardDesc}>{game.description}</p>
            <div style={styles.cardFooter}>
              <span style={styles.playerRange}>
                👥 {game.minPlayers === game.maxPlayers
                  ? `${game.minPlayers} players`
                  : `${game.minPlayers}–${game.maxPlayers} players`}
              </span>
              <button
                style={{
                  ...styles.createBtn,
                  ...(creatingId === game.id ? styles.createBtnDisabled : {}),
                }}
                disabled={creatingId === game.id}
                onClick={() => handleCreate(game.id)}
              >
                {creatingId === game.id ? 'Creating…' : '+ Create Room'}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { padding: '1rem' },
  header: { marginBottom: '1.5rem' },
  title: { color: '#f8fafc', margin: '0 0 0.25rem', fontSize: '1.2rem' },
  subtitle: { color: '#64748b', margin: 0, fontSize: '0.875rem' },
  error: { color: '#f87171', fontSize: '0.85rem', marginBottom: '1rem' },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))',
    gap: '1rem',
  },
  card: {
    background: '#1e293b',
    borderRadius: 12,
    padding: '1.25rem',
    border: '1px solid #334155',
    display: 'flex',
    flexDirection: 'column',
    gap: '0.75rem',
  },
  cardTop: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: '0.75rem',
  },
  cardEmoji: { fontSize: '2.5rem', lineHeight: 1, flexShrink: 0 },
  cardTitle: { color: '#f8fafc', margin: '0 0 0.25rem', fontSize: '1.05rem', fontWeight: 700 },
  tagRow: { display: 'flex', gap: 4, flexWrap: 'wrap' },
  tag: {
    background: '#0f172a',
    color: '#64748b',
    borderRadius: 8,
    padding: '0.1rem 0.45rem',
    fontSize: '0.7rem',
    fontWeight: 500,
  },
  cardDesc: {
    color: '#94a3b8',
    margin: 0,
    fontSize: '0.875rem',
    lineHeight: 1.5,
  },
  cardFooter: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: 'auto',
    paddingTop: '0.5rem',
    borderTop: '1px solid #334155',
  },
  playerRange: { color: '#64748b', fontSize: '0.8rem' },
  createBtn: {
    padding: '0.4rem 1rem',
    background: '#3b82f6',
    color: '#fff',
    border: 'none',
    borderRadius: 8,
    cursor: 'pointer',
    fontWeight: 600,
    fontSize: '0.875rem',
  },
  createBtnDisabled: {
    background: '#334155',
    color: '#64748b',
    cursor: 'not-allowed',
  },
};
