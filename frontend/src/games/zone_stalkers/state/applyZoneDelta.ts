import type { ZoneDelta } from './types';

/** Apply a ZoneDelta patch to the current zone map state blob. Returns a new state. */
export function applyZoneDelta(
  state: Record<string, unknown>,
  delta: ZoneDelta,
): Record<string, unknown> {
  // Apply agent patches
  const agents = { ...(state.agents as Record<string, unknown> ?? {}) };
  for (const [agentId, patch] of Object.entries(delta.changes.agents)) {
    const existing = agents[agentId];
    if (existing && typeof existing === 'object') {
      agents[agentId] = { ...(existing as Record<string, unknown>), ...patch };
    } else {
      agents[agentId] = patch;
    }
  }

  // Apply location patches
  const locations = { ...(state.locations as Record<string, unknown> ?? {}) };
  for (const [locId, patch] of Object.entries(delta.changes.locations)) {
    const existing = locations[locId];
    if (existing && typeof existing === 'object') {
      locations[locId] = { ...(existing as Record<string, unknown>), ...patch };
    } else {
      locations[locId] = patch;
    }
  }

  // Apply trader patches
  const traders = { ...(state.traders as Record<string, unknown> ?? {}) };
  for (const [traderId, patch] of Object.entries(delta.changes.traders)) {
    const existing = traders[traderId];
    if (existing && typeof existing === 'object') {
      traders[traderId] = { ...(existing as Record<string, unknown>), ...patch };
    } else {
      traders[traderId] = patch;
    }
  }

  // Apply state-level changes (game_over, emission_active, active_events, etc.)
  const statePatches = delta.changes.state ?? {};

  return {
    ...state,
    ...statePatches,
    ...delta.world,
    state_revision: delta.revision,
    agents,
    locations,
    traders,
  };
}
