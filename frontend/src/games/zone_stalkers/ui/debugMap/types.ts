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
  artifacts: Array<{ id: string; type: string; name: string; value: number }>;
  items: Array<{ id: string; type: string; name: string }>;
  agents: string[];
  exit_zone?: boolean;
  /** URL of an attached image, served from /media/... */
  image_url?: string | null;
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
  brain_v3_context?: {
    objective_key?: string | null;
    hunt_target_belief?: {
      target_id: string;
      best_location_id: string | null;
      best_location_confidence: number;
      possible_locations: Array<{
        location_id: string;
        probability: number;
        confidence: number;
        freshness: number;
        reason: string;
        source_refs: string[];
      }>;
      likely_routes: Array<{
        from_location_id: string | null;
        to_location_id: string | null;
        confidence: number;
        freshness: number;
        reason: string;
        source_refs: string[];
      }>;
      exhausted_locations: string[];
      lead_count: number;
    } | null;
  } | null;
  memory_v3?: {
    records?: Record<string, {
      id: string;
      kind: string;
      layer: string;
      summary: string;
      details?: Record<string, unknown>;
      location_id?: string | null;
      confidence?: number;
      created_turn?: number;
    }>;
  } | null;
}

export interface ZoneMapState {
  context_type: string;
  world_turn: number;
  world_hour: number;
  world_minute: number;
  world_day: number;
  max_turns?: number;
  /** Emission (Выброс) mechanic */
  emission_active: boolean;
  emission_scheduled_turn: number;
  emission_ends_turn: number;
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
      effects?: Record<string, unknown>;
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
  /** True while the server-side auto-ticker is advancing this match (core flag) */
  auto_tick_enabled?: boolean;
  /** Current tick speed: "realtime" | "x10" | "x100" (null when stopped) */
  auto_tick_speed?: string | null;
  /** @deprecated Legacy slow-mode flag (kept for backward compat) */
  auto_tick_slow_mode?: boolean;
}

export interface DebugMapPageProps {
  /** Match ID — used to persist viewport position per-match in localStorage */
  matchId: string;
  zoneState: ZoneMapState;
  currentLocId: string | null;
  /** Send a command to the backend (uses the zone_map context id automatically) */
  sendCommand: (cmd: string, payload: Record<string, unknown>) => Promise<void>;
  /** Zone-map context ID — forwarded to AgentProfileModal for on-demand memory loading. */
  contextId?: string;
}
