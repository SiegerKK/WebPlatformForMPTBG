import React, { useEffect, useState } from 'react';
import { useAppState } from './store';
import { authApi } from './api/client';
import Login from './components/Login/Login';
import Layout from './components/Layout/Layout';
import MatchList from './components/MatchList/MatchList';
import TicTacToeGame from './components/TicTacToeGame';
import type { User, Match } from './types';

type View = 'matches' | 'match';

export default function App() {
  const { state, dispatch } = useAppState();
  const [view, setView] = useState<View>('matches');

  // Restore user session on mount if token exists
  useEffect(() => {
    if (state.token && !state.user) {
      authApi
        .me()
        .then((res) => dispatch({ type: 'SET_USER', payload: res.data as User }))
        .catch(() => dispatch({ type: 'LOGOUT' }));
    }
  }, []);

  // Switch to match view when a match is selected
  useEffect(() => {
    if (!state.currentMatch) return;
    setView('match');
  }, [state.currentMatch?.id]);

  if (!state.token) {
    return <Login />;
  }

  const isTicTacToe = state.currentMatch?.game_id === 'tictactoe';

  return (
    <Layout onNavSelect={(v) => setView(v as View)} activeView={view}>
      {view === 'matches' && <MatchList />}
      {view === 'match' && (
        <div>
          {!state.currentMatch ? (
            <p style={styles.hint}>Select a match from the Matches list.</p>
          ) : isTicTacToe && state.user ? (
            <TicTacToeGame
              match={state.currentMatch}
              user={state.user}
              onMatchUpdated={(updated: Match) =>
                dispatch({ type: 'SET_CURRENT_MATCH', payload: updated })
              }
            />
          ) : (
            <div style={styles.matchView}>
              <div style={styles.matchHeader}>
                <h2 style={styles.matchTitle}>
                  {state.currentMatch.game_id}
                  <span style={styles.matchStatus}> — {state.currentMatch.status}</span>
                </h2>
                <p style={styles.matchId}>Match ID: {state.currentMatch.id}</p>
              </div>
              <p style={styles.hint}>
                No dedicated UI for game <strong>{state.currentMatch.game_id}</strong> yet.
              </p>
            </div>
          )}
        </div>
      )}
    </Layout>
  );
}

const styles: Record<string, React.CSSProperties> = {
  matchView: { display: 'flex', flexDirection: 'column', gap: '1.5rem' },
  hint: { color: '#64748b', textAlign: 'center', marginTop: '3rem' },
  matchHeader: {},
  matchTitle: { color: '#f8fafc', margin: 0, fontSize: '1.3rem' },
  matchStatus: { color: '#94a3b8', fontWeight: 400 },
  matchId: { color: '#475569', fontSize: '0.8rem', margin: '0.25rem 0 0' },
};
