// ─── Shared types for the Debug Map ─────────────────────────────────────────
import type { ZoneDebugState, ZoneDebugSubscription } from '../../state/types';
export type { ZoneDebugState, ZoneDebugSubscription };

export interface LocationConn {
  to: string;
  type: string;
  travel_time?: number;
  closed?: boolean;
}

// ─── Image slots ─────────────────────────────────────────────────────────────

export type LocationImageSlot = "clear" | "fog" | "rain" | "night_clear" | "night_rain";

export type LocationImageSlots = Partial<Record<LocationImageSlot, string | null>>;

export const LOCATION_IMAGE_SLOTS: LocationImageSlot[] = [
  "clear",
  "fog",
  "rain",
  "night_clear",
  "night_rain",
];

export const LOCATION_IMAGE_SLOT_LABELS: Record<LocationImageSlot, string> = {
  clear: "Ясно",
  fog: "Туман",
  rain: "Дождь",
  night_clear: "Ночь ясно",
  night_rain: "Ночь дождь",
};

export const LOCATION_IMAGE_SLOT_ICONS: Record<LocationImageSlot, string> = {
  clear: "☀️",
  fog: "🌫️",
  rain: "🌧️",
  night_clear: "🌙",
  night_rain: "🌧️🌙",
};

/**
 * Returns the URL of the primary image for a location, with fallback logic.
 * This is the canonical function to use everywhere instead of `loc.image_url`.
 */
export function getPrimaryLocationImageUrl(loc: ZoneLocation): string | null {
  const slot = loc.primary_image_slot;
  if (slot && loc.image_slots?.[slot]) return loc.image_slots[slot] ?? null;
  // Legacy fallback: if image_url is set and no slots are populated, use it
  if (loc.image_url && !loc.image_slots) return loc.image_url;
  if (loc.image_url && Object.keys(loc.image_slots ?? {}).length === 0) return loc.image_url;
  // Scan all slots in order for any available image
  for (const key of LOCATION_IMAGE_SLOTS) {
    const url = loc.image_slots?.[key];
    if (url) return url;
  }
  // Final fallback to raw image_url
  if (loc.image_url) return loc.image_url;
  return null;
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
  /** Legacy single-image URL — kept for backward compat. Use getPrimaryLocationImageUrl(). */
  image_url?: string | null;
  /** Per-weather-condition image slots. */
  image_slots?: LocationImageSlots;
  /** Which slot is the current primary image. */
  primary_image_slot?: LocationImageSlot | null;
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
  debug?: {
    hunt_search_by_agent?: Record<string, {
      hunter_id: string;
      hunter_name: string;
      target_id: string;
      target_name?: string;
      best_location_id?: string | null;
      best_location_confidence?: number;
      possible_locations?: Array<{
        location_id: string;
        probability: number;
        confidence: number;
        freshness: number;
        reason: string;
        source_refs: string[];
      }>;
      likely_routes?: Array<{
        from_location_id: string | null;
        to_location_id: string | null;
        confidence: number;
        freshness: number;
        reason: string;
        source_refs: string[];
      }>;
      exhausted_locations?: string[];
      lead_count?: number;
      current_objective?: string | null;
      current_plan_target_location_id?: string | null;
    }>;
    location_hunt_traces?: Record<string, {
      location_id: string;
      positive_leads: Array<{
        id: string;
        kind: string;
        hunter_id: string;
        target_id?: string | null;
        source_agent_id?: string | null;
        summary: string;
        confidence: number;
        freshness: number;
        turn: number;
        source_ref: string;
      }>;
      negative_leads: Array<{
        id: string;
        kind: string;
        hunter_id: string;
        target_id?: string | null;
        source_agent_id?: string | null;
        source_kind?: string | null;
        summary: string;
        confidence: number;
        freshness: number;
        turn: number;
        source_ref: string;
        failed_search_count?: number;
        cooldown_until_turn?: number | null;
      }>;
      routes_in: Array<{
        hunter_id: string;
        target_id?: string | null;
        from_location_id: string | null;
        to_location_id: string | null;
        source_agent_id?: string | null;
        confidence: number;
        freshness: number;
        reason: string;
        source_ref: string;
        turn: number;
      }>;
      routes_out: Array<{
        hunter_id: string;
        target_id?: string | null;
        from_location_id: string | null;
        to_location_id: string | null;
        source_agent_id?: string | null;
        confidence: number;
        freshness: number;
        reason: string;
        source_ref: string;
        turn: number;
      }>;
      is_exhausted_for: Array<{
        hunter_id: string;
        target_id?: string | null;
        source_agent_id?: string | null;
        source_kind?: string | null;
        failed_search_count?: number;
        cooldown_until_turn?: number | null;
        source_ref?: string;
        turn?: number;
        freshness?: number;
      }>;
      combat_hunt_events?: Array<{
        kind: string;
        hunter_id: string;
        target_id?: string | null;
        source_agent_id?: string | null;
        summary: string;
        confidence: number;
        freshness: number;
        turn: number;
        source_ref: string;
      }>;
      lead_count?: number;
      route_count?: number;
      event_count?: number;
    }>;
  };
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
  /** Live debug state populated by zone_debug_delta WebSocket messages. */
  debugState?: ZoneDebugState;
  /** Subscribe to zone debug deltas via WebSocket. */
  subscribeZoneDebug?: (subscription: ZoneDebugSubscription) => void;
  /** Unsubscribe from zone debug deltas. */
  unsubscribeZoneDebug?: () => void;
}
