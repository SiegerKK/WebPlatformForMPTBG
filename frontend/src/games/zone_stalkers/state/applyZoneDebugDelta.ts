import type { ZoneDebugDelta, ZoneDebugState } from './types';

export function applyZoneDebugDelta(
  debugState: ZoneDebugState,
  delta: ZoneDebugDelta,
): ZoneDebugState {
  return {
    ...debugState,
    debugRevision: delta.debug_revision,
    huntSearchByAgent: delta.changes.hunt_search_by_agent
      ? { ...debugState.huntSearchByAgent, ...delta.changes.hunt_search_by_agent }
      : debugState.huntSearchByAgent,
    locationHuntTraces: delta.changes.location_hunt_traces
      ? { ...debugState.locationHuntTraces, ...delta.changes.location_hunt_traces }
      : debugState.locationHuntTraces,
    selectedAgentProfile: delta.changes.selected_agent_profile_summary
      ? { ...(debugState.selectedAgentProfile ?? {}), ...delta.changes.selected_agent_profile_summary }
      : debugState.selectedAgentProfile,
    selectedLocationProfile: delta.changes.selected_location_summary
      ? { ...(debugState.selectedLocationProfile ?? {}), ...delta.changes.selected_location_summary }
      : debugState.selectedLocationProfile,
  };
}
