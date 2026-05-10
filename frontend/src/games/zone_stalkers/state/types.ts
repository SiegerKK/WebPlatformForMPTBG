export interface AgentDeltaPatch {
  location_id?: string | null;
  is_alive?: boolean;
  has_left_zone?: boolean;
  hp?: number;
  hunger?: number;
  thirst?: number;
  sleepiness?: number;
  money?: number;
  current_goal?: string | null;
  global_goal?: string | null;
  action_used?: boolean;
  scheduled_action?: Record<string, unknown> | null;
  active_plan_summary?: Record<string, unknown> | null;
  equipment_summary?: Record<string, unknown> | null;
  inventory_summary?: Array<Record<string, unknown>> | null;
}

export interface LocationDeltaPatch {
  agents?: string[];
  artifact_count?: number;
  item_count?: number;
  artifacts?: Array<Record<string, unknown>>;
  items?: Array<Record<string, unknown>>;
  anomaly_activity?: number;
  dominant_anomaly_type?: string | null;
}

export interface ZoneDeltaChanges {
  agents: Record<string, AgentDeltaPatch>;
  locations: Record<string, LocationDeltaPatch>;
  traders: Record<string, Record<string, unknown>>;
  state: Record<string, unknown>;
}

export interface ZoneDeltaWorld {
  world_turn: number;
  world_day: number;
  world_hour: number;
  world_minute: number;
}

export interface ZoneDeltaEvents {
  count: number;
  preview: Array<{
    event_type: string;
    agent_id?: string;
    location_id?: string;
    summary?: string;
    action_kind?: string;
  }>;
}

export interface ZoneDelta {
  base_revision: number;
  revision: number;
  world: ZoneDeltaWorld;
  changes: ZoneDeltaChanges;
  events: ZoneDeltaEvents;
}

export interface ZoneDeltaMessage {
  type: 'zone_delta';
  match_id: string;
  context_id: string;
  base_revision: number;
  revision: number;
  world: ZoneDeltaWorld;
  changes: ZoneDeltaChanges;
  events: ZoneDeltaEvents;
}

export interface ZoneDebugState {
  huntSearchByAgent: Record<string, Record<string, unknown>>;
  locationHuntTraces: Record<string, Record<string, unknown>>;
  selectedAgentProfile: Record<string, unknown> | null;
  selectedLocationProfile: Record<string, unknown> | null;
  debugRevision: number;
}

export interface ZoneDebugDelta {
  type: 'zone_debug_delta';
  match_id: string;
  context_id: string;
  base_revision: number;
  revision: number;
  debug_revision: number;
  scope: {
    mode: string;
    hunter_id?: string | null;
    target_id?: string | null;
  };
  changes: {
    hunt_search_by_agent?: Record<string, Record<string, unknown>>;
    location_hunt_traces?: Record<string, Record<string, unknown>>;
    agent_brain_summary?: Record<string, unknown>;
    selected_agent_profile_summary?: Record<string, unknown>;
    selected_location_summary?: Record<string, unknown>;
  };
}

export interface ZoneDebugSubscription {
  context_id: string;
  mode: 'debug-map' | 'agent-profile' | 'location-profile';
  selected_agent_id?: string | null;
  selected_location_id?: string | null;
  hunter_id?: string | null;
  target_id?: string | null;
  visible_location_ids?: string[];
  min_confidence?: number;
  freshness_window?: number;
}
