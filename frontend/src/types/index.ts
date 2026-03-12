export interface User {
  id: string;
  username: string;
  email: string;
  is_active: boolean;
  is_bot: boolean;
  is_superuser: boolean;
  created_at: string;
}

export interface Token {
  access_token: string;
  token_type: string;
}

export type MatchStatus =
  | 'draft'
  | 'waiting_for_players'
  | 'initializing'
  | 'active'
  | 'paused'
  | 'finished'
  | 'archived'
  | 'failed';

export interface Match {
  id: string;
  game_id: string;
  title?: string;
  status: MatchStatus;
  /** user id of the creator — matches backend field `created_by_user_id` */
  created_by_user_id: string;
  settings: Record<string, unknown>;
  seed: string;
  created_at: string;
  started_at?: string;
  finished_at?: string;
}

export interface MatchParticipant {
  id: string;
  match_id: string;
  user_id?: string;
  role: string;
  side_id?: string;
  kind: 'human' | 'bot' | 'neutral' | 'system';
  status: string;
  display_name?: string;
  is_ready: boolean;
  joined_at: string;
}

export type ContextStatus =
  | 'created'
  | 'initializing'
  | 'active'
  | 'resolving'
  | 'suspended'
  | 'finished'
  | 'failed'
  | 'archived';

export interface GameContext {
  id: string;
  match_id: string;
  parent_id?: string;
  parent_context_id?: string;
  context_type: string;
  label?: string;
  status: ContextStatus;
  /** The JSON game state object stored in the context (backend field: state_blob) */
  state_blob: Record<string, unknown>;
  state_version: number;
  depth: number;
  created_at: string;
  started_at?: string;
  finished_at?: string;
  result_blob?: Record<string, unknown>;
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
  causation_command_id?: string;
  /** Sequence number — backend field: sequence_no */
  sequence_no: number;
  created_at: string;
}

export type TurnMode = 'strict' | 'simultaneous' | 'wego' | 'hybrid';
export type TurnStatus = 'waiting_for_players' | 'resolving' | 'resolved';

export interface TurnState {
  id: string;
  context_id: string;
  turn_number: number;
  mode: TurnMode;
  status: TurnStatus;
  active_side_id?: string;
  deadline_at?: string;
  submitted_players: string[];
  opened_at?: string;
  resolved_at?: string;
}

export interface CommandResult {
  command_id: string;
  status: string;
  events: Array<{ event_type: string; payload: Record<string, unknown> }>;
  error?: string;
}

export interface UserProfile {
  id: string;
  username: string;
  email: string;
  is_active: boolean;
  is_bot: boolean;
  is_superuser: boolean;
  created_at: string;
  matches_created: number;
  matches_played: number;
}

/** Shape of state_blob for a Tic-Tac-Toe context */
export interface TicTacToeState {
  board: Array<'X' | 'O' | null>;
  player_marks: Record<string, 'X' | 'O'>;
  current_player_id: string | null;
  winner: string | null;
  winner_mark: 'X' | 'O' | null;
  game_over: boolean;
  turn_count: number;
}
