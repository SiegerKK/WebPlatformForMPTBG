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

export const WEATHER_IMAGE_SLOTS = ['clear', 'fog', 'rain', 'night_clear', 'night_rain'] as const;
export const PSI_IMAGE_SLOTS = ['low', 'medium', 'high', 'critical', 'max'] as const;
export const UNDERGROUND_IMAGE_SLOTS = [
  'default',
  'dark',
  'emergency_light',
  'power_failure',
  'flooded',
  'toxic',
  'anomaly',
  'psi_low',
  'psi_high',
  'combat',
] as const;

export type WeatherSlot = (typeof WEATHER_IMAGE_SLOTS)[number];
export type PsiSlot = (typeof PSI_IMAGE_SLOTS)[number];
export type UndergroundSlot = (typeof UNDERGROUND_IMAGE_SLOTS)[number];
export type LocationImageSlot = WeatherSlot;

export const LOCATION_IMAGE_GROUPS = ['normal', 'gloom', 'anomaly', 'psi', 'underground'] as const;
export type LocationImageGroup = (typeof LOCATION_IMAGE_GROUPS)[number];

export type LocationImageRef = {
  group: LocationImageGroup;
  slot: string;
};

export type LocationImageProfile = {
  is_anomalous?: boolean;
  is_psi?: boolean;
  is_underground?: boolean;
};

export type LocationImageSlots = Partial<Record<LocationImageSlot, string | null>>;

export type LocationImageSlotsV2 = {
  normal?: Partial<Record<WeatherSlot, string | null>>;
  gloom?: Partial<Record<WeatherSlot, string | null>>;
  anomaly?: Partial<Record<WeatherSlot, string | null>>;
  psi?: Partial<Record<PsiSlot, string | null>>;
  underground?: Partial<Record<UndergroundSlot, string | null>>;
};

export const LOCATION_IMAGE_GROUP_SLOT_MAP: Record<LocationImageGroup, readonly string[]> = {
  normal: WEATHER_IMAGE_SLOTS,
  gloom: WEATHER_IMAGE_SLOTS,
  anomaly: WEATHER_IMAGE_SLOTS,
  psi: PSI_IMAGE_SLOTS,
  underground: UNDERGROUND_IMAGE_SLOTS,
};

export const LOCATION_IMAGE_GROUP_LABELS: Record<LocationImageGroup, string> = {
  normal: 'Обычная',
  gloom: 'Мрачная',
  anomaly: 'Аномальная',
  psi: 'Пси',
  underground: 'Подземная',
};

export const LOCATION_IMAGE_SLOTS: LocationImageSlot[] = [...WEATHER_IMAGE_SLOTS];

export const LOCATION_IMAGE_SLOT_LABELS: Record<LocationImageSlot, string> = {
  clear: 'Ясно',
  fog: 'Туман',
  rain: 'Дождь',
  night_clear: 'Ночь ясно',
  night_rain: 'Ночь дождь',
};

export const LOCATION_IMAGE_SLOT_ICONS: Record<LocationImageSlot, string> = {
  clear: '☀️',
  fog: '🌫️',
  rain: '🌧️',
  night_clear: '🌙',
  night_rain: '🌧️🌙',
};

const PSI_IMAGE_SLOT_LABELS: Record<PsiSlot, string> = {
  low: 'Низкая',
  medium: 'Средняя',
  high: 'Высокая',
  critical: 'Критическая',
  max: 'Максимум',
};

const PSI_IMAGE_SLOT_ICONS: Record<PsiSlot, string> = {
  low: '🧠',
  medium: '🧠',
  high: '🧠',
  critical: '🧠',
  max: '🧠',
};

const UNDERGROUND_IMAGE_SLOT_LABELS: Record<UndergroundSlot, string> = {
  default: 'Обычный',
  dark: 'Тёмный',
  emergency_light: 'Аварийный свет',
  power_failure: 'Без света',
  flooded: 'Затоплен',
  toxic: 'Токсично',
  anomaly: 'Аномалии',
  psi_low: 'Пси слабый',
  psi_high: 'Пси сильный',
  combat: 'Бой',
};

const UNDERGROUND_IMAGE_SLOT_ICONS: Record<UndergroundSlot, string> = {
  default: '🕳️',
  dark: '🌑',
  emergency_light: '🚨',
  power_failure: '⚡',
  flooded: '🌊',
  toxic: '☣️',
  anomaly: '⚠️',
  psi_low: '🧠',
  psi_high: '🧠',
  combat: '⚔️',
};

export function getImageSlotLabel(group: LocationImageGroup, slot: string): string {
  if (group === 'psi') return PSI_IMAGE_SLOT_LABELS[slot as PsiSlot] ?? slot;
  if (group === 'underground') return UNDERGROUND_IMAGE_SLOT_LABELS[slot as UndergroundSlot] ?? slot;
  return LOCATION_IMAGE_SLOT_LABELS[slot as LocationImageSlot] ?? slot;
}

export function getImageSlotIcon(group: LocationImageGroup, slot: string): string {
  if (group === 'psi') return PSI_IMAGE_SLOT_ICONS[slot as PsiSlot] ?? '🖼️';
  if (group === 'underground') return UNDERGROUND_IMAGE_SLOT_ICONS[slot as UndergroundSlot] ?? '🕳️';
  return LOCATION_IMAGE_SLOT_ICONS[slot as LocationImageSlot] ?? '🖼️';
}

export function getEnabledImageGroups(profile?: LocationImageProfile): LocationImageGroup[] {
  if (profile?.is_underground) {
    const groups: LocationImageGroup[] = ['underground'];
    if (profile?.is_anomalous) groups.push('anomaly');
    if (profile?.is_psi) groups.push('psi');
    return groups;
  }

  const groups: LocationImageGroup[] = ['normal', 'gloom'];
  if (profile?.is_anomalous) groups.push('anomaly');
  if (profile?.is_psi) groups.push('psi');
  return groups;
}

export function getRequiredImageGroups(profile?: LocationImageProfile): LocationImageGroup[] {
  return profile?.is_underground ? ['underground'] : ['normal', 'gloom'];
}

export function normalizeImageSlotsV2(loc: ZoneLocation): LocationImageSlotsV2 {
  const out: LocationImageSlotsV2 = {};
  const raw = loc.image_slots_v2 ?? {};

  for (const group of LOCATION_IMAGE_GROUPS) {
    const groupSlots = raw[group];
    const normalized: Record<string, string | null> = {};
    for (const slot of LOCATION_IMAGE_GROUP_SLOT_MAP[group]) {
      normalized[slot] = (groupSlots?.[slot as keyof typeof groupSlots] as string | null | undefined) ?? null;
    }
    (out as Record<string, Record<string, string | null>>)[group] = normalized;
  }

  // Legacy compatibility only if no v2 slots at all.
  const hasAnyV2 = LOCATION_IMAGE_GROUPS.some((group) => {
    const g = raw[group];
    if (!g) return false;
    return Object.values(g).some((v) => Boolean(v));
  });
  if (!hasAnyV2) {
    const legacy = loc.image_slots ?? {};
    for (const slot of WEATHER_IMAGE_SLOTS) {
      const val = legacy[slot as LocationImageSlot] ?? null;
      if (val) {
        (out.normal as Record<string, string | null>)[slot] = val;
      }
    }
  }

  return out;
}

export function getImageSlotUrl(slots: LocationImageSlotsV2, ref?: LocationImageRef | null): string | null {
  if (!ref) return null;
  const groupSlots = slots[ref.group];
  if (!groupSlots) return null;
  return (groupSlots as Record<string, string | null | undefined>)[ref.slot] ?? null;
}

export function getFallbackImageRefs(loc: ZoneLocation): LocationImageRef[] {
  const profile = loc.image_profile;
  if (profile?.is_underground) {
    const refs: LocationImageRef[] = UNDERGROUND_IMAGE_SLOTS.map((slot) => ({ group: 'underground', slot }));
    if (profile?.is_psi) refs.push({ group: 'psi', slot: 'low' });
    if (profile?.is_anomalous) refs.push({ group: 'anomaly', slot: 'clear' });
    return refs;
  }

  const refs: LocationImageRef[] = [
    { group: 'normal', slot: 'clear' },
    { group: 'gloom', slot: 'clear' },
    { group: 'normal', slot: 'fog' },
    { group: 'normal', slot: 'rain' },
    { group: 'normal', slot: 'night_clear' },
    { group: 'normal', slot: 'night_rain' },
    { group: 'gloom', slot: 'fog' },
    { group: 'gloom', slot: 'rain' },
    { group: 'gloom', slot: 'night_clear' },
    { group: 'gloom', slot: 'night_rain' },
  ];
  if (profile?.is_anomalous) refs.push(...WEATHER_IMAGE_SLOTS.map((slot) => ({ group: 'anomaly' as const, slot })));
  if (profile?.is_psi) refs.push({ group: 'psi', slot: 'low' });
  return refs;
}

export function getPrimaryLocationImageUrlV2(loc: ZoneLocation): string | null {
  const slots = normalizeImageSlotsV2(loc);
  const fromRef = getImageSlotUrl(slots, loc.primary_image_ref ?? null);
  if (fromRef) return fromRef;

  for (const fallbackRef of getFallbackImageRefs(loc)) {
    const fallback = getImageSlotUrl(slots, fallbackRef);
    if (fallback) return fallback;
  }

  // Only use image_url as a legacy fallback when no v2 schema has ever been written
  // to this location. Once image_slots_v2 exists, image_url is derived output only
  // and must not be used as a source of truth (it may be stale after a delete).
  const hasExplicitV2Schema = Boolean(loc.image_slots_v2);
  const hasAnyV2Image = LOCATION_IMAGE_GROUPS.some((group) => {
    const groupSlots = loc.image_slots_v2?.[group];
    return groupSlots != null && Object.values(groupSlots).some(Boolean);
  });
  if (!hasExplicitV2Schema && !hasAnyV2Image) {
    return loc.image_url ?? null;
  }

  return null;
}

/**
 * Returns the URL of the primary image for a location, with fallback logic.
 */
export function getPrimaryLocationImageUrl(loc: ZoneLocation): string | null {
  return getPrimaryLocationImageUrlV2(loc);
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
  image_profile?: LocationImageProfile;
  image_slots_v2?: LocationImageSlotsV2;
  primary_image_ref?: LocationImageRef | null;
  /** Legacy single-image URL — kept for backward compat. */
  image_url?: string | null;
  /** Legacy per-weather-condition image slots. */
  image_slots?: LocationImageSlots;
  /** Legacy primary weather slot. */
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
