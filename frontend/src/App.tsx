import React, { useEffect, useState } from 'react';
import { useAppState } from './store';
import { authApi, eventsApi, contextsApi } from './api/client';
import Login from './components/Login/Login';
import Layout from './components/Layout/Layout';
import MatchList from './components/MatchList/MatchList';
import ContextTree from './components/ContextTree/ContextTree';
import EventLog from './components/EventLog/EventLog';
import type { User, GameContext, GameEvent } from './types';

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

  // Load contexts & events when a match is selected
  useEffect(() => {
    if (!state.currentMatch) return;

    contextsApi
      .getTree(state.currentMatch.id)
      .then((res) => dispatch({ type: 'SET_CONTEXTS', payload: res.data as GameContext[] }))
      .catch(() => dispatch({ type: 'SET_CONTEXTS', payload: [] }));

    eventsApi
      .listForMatch(state.currentMatch.id)
      .then((res) => dispatch({ type: 'SET_EVENTS', payload: res.data as GameEvent[] }))
      .catch(() => dispatch({ type: 'SET_EVENTS', payload: [] }));

    setView('match');
  }, [state.currentMatch?.id]);

  if (!state.token) {
    return <Login />;
  }

  return (
    <Layout onNavSelect={(v) => setView(v as View)} activeView={view}>
      {view === 'matches' && <MatchList />}
      {view === 'match' && (
        <div style={styles.matchView}>
          {!state.currentMatch ? (
            <p style={styles.hint}>Select a match from the Matches list.</p>
          ) : (
            <>
              <div style={styles.matchHeader}>
                <h2 style={styles.matchTitle}>
                  {state.currentMatch.game_id}
                  <span style={styles.matchStatus}> — {state.currentMatch.status}</span>
                </h2>
                <p style={styles.matchId}>Match ID: {state.currentMatch.id}</p>
              </div>

              <div style={styles.columns}>
                <div style={styles.column}>
                  <h3 style={styles.sectionTitle}>Context Tree</h3>
                  <ContextTree
                    contexts={state.contexts}
                    onSelect={(_ctx) => {
                      // Context selection is a no-op at match-level view;
                      // individual game UIs can extend this as needed.
                    }}
                  />
                </div>

                <div style={{ ...styles.column, flex: 2 }}>
                  <h3 style={styles.sectionTitle}>Event Log</h3>
                  <EventLog events={state.events} maxHeight={400} />
                </div>
              </div>
            </>
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
  columns: { display: 'flex', gap: '1.5rem', alignItems: 'flex-start' },
  column: { flex: 1, display: 'flex', flexDirection: 'column', gap: 8 },
  sectionTitle: { color: '#94a3b8', fontSize: '0.85rem', textTransform: 'uppercase', margin: 0, letterSpacing: '0.06em' },
};
