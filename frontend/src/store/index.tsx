import React, { createContext, useContext, useReducer } from 'react';
import type { User, Match, GameContext, Entity, GameEvent, TurnState } from '../types';

interface AppState {
  user: User | null;
  token: string | null;
  matches: Match[];
  currentMatch: Match | null;
  contexts: GameContext[];
  entities: Entity[];
  events: GameEvent[];
  currentTurn: TurnState | null;
  loading: boolean;
  error: string | null;
}

type Action =
  | { type: 'SET_USER'; payload: User | null }
  | { type: 'SET_TOKEN'; payload: string | null }
  | { type: 'SET_MATCHES'; payload: Match[] }
  | { type: 'SET_CURRENT_MATCH'; payload: Match | null }
  | { type: 'SET_CONTEXTS'; payload: GameContext[] }
  | { type: 'SET_ENTITIES'; payload: Entity[] }
  | { type: 'SET_EVENTS'; payload: GameEvent[] }
  | { type: 'SET_CURRENT_TURN'; payload: TurnState | null }
  | { type: 'SET_LOADING'; payload: boolean }
  | { type: 'SET_ERROR'; payload: string | null }
  | { type: 'LOGOUT' };

const initialState: AppState = {
  user: null,
  token: localStorage.getItem('access_token'),
  matches: [],
  currentMatch: null,
  contexts: [],
  entities: [],
  events: [],
  currentTurn: null,
  loading: false,
  error: null,
};

function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case 'SET_USER': return { ...state, user: action.payload };
    case 'SET_TOKEN': return { ...state, token: action.payload };
    case 'SET_MATCHES': return { ...state, matches: action.payload };
    case 'SET_CURRENT_MATCH': return { ...state, currentMatch: action.payload };
    case 'SET_CONTEXTS': return { ...state, contexts: action.payload };
    case 'SET_ENTITIES': return { ...state, entities: action.payload };
    case 'SET_EVENTS': return { ...state, events: action.payload };
    case 'SET_CURRENT_TURN': return { ...state, currentTurn: action.payload };
    case 'SET_LOADING': return { ...state, loading: action.payload };
    case 'SET_ERROR': return { ...state, error: action.payload };
    case 'LOGOUT':
      localStorage.removeItem('access_token');
      return { ...initialState, token: null };
    default: return state;
  }
}

const AppContext = createContext<{
  state: AppState;
  dispatch: React.Dispatch<Action>;
} | undefined>(undefined);

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  return (
    <AppContext.Provider value={{ state, dispatch }}>
      {children}
    </AppContext.Provider>
  );
}

export function useAppState() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useAppState must be used within AppProvider');
  return ctx;
}
