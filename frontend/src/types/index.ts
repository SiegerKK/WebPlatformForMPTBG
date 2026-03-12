export interface User {
  id: string;
  username: string;
  email: string;
  is_active: boolean;
  is_bot: boolean;
  created_at: string;
}

export interface Token {
  access_token: string;
  token_type: string;
}

export type MatchStatus = 'waiting' | 'active' | 'paused' | 'finished' | 'cancelled';

export interface Match {
  id: string;
  game_id: string;
  status: MatchStatus;
  created_by: string;
  config: Record<string, unknown>;
  seed: string;
  created_at: string;
  started_at?: string;
  finished_at?: string;
}

export interface MatchParticipant {
  id: string;
  match_id: string;
  user_id: string;
  role: string;
  faction?: string;
  is_active: boolean;
  joined_at: string;
}

export type ContextStatus = 'pending' | 'active' | 'resolved' | 'archived';

export interface GameContext {
  id: string;
  match_id: string;
  parent_id?: string;
  context_type: string;
  status: ContextStatus;
  state: Record<string, unknown>;
  state_version: number;
  config: Record<string, unknown>;
  created_at: string;
  resolved_at?: string;
}

export interface Entity {
  id: string;
  context_id: string;
  owner_id?: string;
  archetype: string;
  components: Record<string, unknown>;
  tags: string[];
  visibility: string;
  version: number;
  is_active: boolean;
  created_at: string;
}

export interface GameEvent {
  id: string;
  match_id: string;
  context_id: string;
  event_type: string;
  payload: Record<string, unknown>;
  caused_by_command_id?: string;
  sequence_number: number;
  created_at: string;
}

export type TurnMode = 'strict' | 'simultaneous' | 'async_window';
export type TurnStatus = 'waiting_for_players' | 'resolving' | 'resolved';

export interface TurnState {
  id: string;
  context_id: string;
  turn_number: number;
  mode: TurnMode;
  status: TurnStatus;
  active_player_id?: string;
  deadline?: string;
  submitted_players: string[];
  created_at: string;
  resolved_at?: string;
}

export interface CommandResult {
  success: boolean;
  command_id: string;
  events: GameEvent[];
  error?: string;
}
