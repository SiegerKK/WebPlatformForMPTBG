// ─── Shared types for the Debug Map ─────────────────────────────────────────

export interface LocationConn {
  to: string;
  type: string;
  travel_time?: number;
  closed?: boolean;
}

export interface ZoneLocation {
  id: string;
  name: string;
  region?: string;
  terrain_type?: string;
  anomaly_activity?: number;
  dominant_anomaly_type?: string | null;
  connections: LocationConn[];
  anomalies: Array<{ id: string; type: string; name: string; active?: boolean }>;
  artifacts: Array<{ id: string; type: string; name: string; value: number }>;
  items: Array<{ id: string; type: string; name: string }>;
  agents: string[];
}

export interface StalkerAgent {
  id: string;
  name: string;
  location_id: string;
  hp: number;
  max_hp: number;
  faction: string;
  is_alive: boolean;
  controller: { kind: string; participant_id?: string | null };
  scheduled_action?: {
    type: string;
    target_id?: string;
    turns_remaining?: number;
  } | null;
}

export interface ZoneMapState {
  context_type: string;
  world_turn: number;
  world_hour: number;
  world_minute: number;
  world_day: number;
  locations: Record<string, ZoneLocation>;
  agents: Record<string, StalkerAgent>;
  mutants: Record<string, { id: string; name: string; location_id: string; hp: number; max_hp: number; is_alive: boolean }>;
  traders: Record<string, {
    id: string;
    name: string;
    location_id: string;
    money?: number;
    inventory?: Array<{ id: string; type: string; name: string; value?: number }>;
    memory?: Array<{
      world_turn: number;
      world_day: number;
      world_hour: number;
      world_minute?: number;
      type: string;
      title: string;
      summary: string;
    }>;
  }>;
  player_agents: Record<string, string>;
  active_events: string[];
  game_over: boolean;
  /** Persisted debug canvas state written by the debug_update_map command */
  debug_layout?: {
    positions: Record<string, { x: number; y: number }>;
    regions?: Record<string, { name: string; colorIndex: number }>;
  };
}

export interface DebugMapPageProps {
  zoneState: ZoneMapState;
  currentLocId: string | null;
  /** Send a command to the backend (uses the zone_map context id automatically) */
  sendCommand: (cmd: string, payload: Record<string, unknown>) => Promise<void>;
}
