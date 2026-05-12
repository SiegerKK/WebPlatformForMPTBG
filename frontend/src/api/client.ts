import axios from 'axios';

const apiClient = axios.create({
  baseURL: '/api',
});

apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // Token expired or invalid — clear session and reload to show login screen
      localStorage.removeItem('access_token');
      alert('Ваша сессия истекла. Пожалуйста, войдите снова.');
      window.location.reload();
    }
    return Promise.reject(error);
  },
);

export const authApi = {
  register: (data: { username: string; email: string; password: string }) =>
    apiClient.post('/auth/register', data),
  login: (username: string, password: string) =>
    apiClient.post('/auth/login', new URLSearchParams({ username, password }), {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    }),
  me: () => apiClient.get('/auth/me'),
};

export const matchesApi = {
  list: () => apiClient.get('/matches'),
  create: (data: { game_id: string; title?: string; config?: Record<string, unknown> }) =>
    apiClient.post('/matches', data),
  get: (id: string) => apiClient.get(`/matches/${id}`),
  join: (id: string) => apiClient.post(`/matches/${id}/join`, {}),
  start: (id: string) => apiClient.post(`/matches/${id}/start`),
  delete: (id: string) => apiClient.delete(`/matches/${id}`),
  purge: (id: string) => apiClient.delete(`/matches/${id}/purge`),
  participants: (id: string) => apiClient.get(`/matches/${id}/participants`),
  tick: (id: string) => apiClient.post(`/matches/${id}/tick`),
};

export const contextsApi = {
  create: (data: {
    match_id: string;
    context_type: string;
    parent_id?: string;
    config?: Record<string, unknown>;
  }) => apiClient.post('/contexts', data),
  get: (id: string) => apiClient.get(`/contexts/${id}`),
  getTree: (matchId: string) => apiClient.get(`/matches/${matchId}/contexts`),
  getProjection: (id: string) => apiClient.get(`/contexts/${id}/projection`),
  /** Fetch Zone Stalkers game projection (avoids full state deepcopy on backend). */
  getZoneProjection: (id: string, mode: 'game' | 'debug-map' | 'full' = 'game') =>
    apiClient.get(`/zone-stalkers/contexts/${id}/projection`, { params: { mode } }),
  /** Fetch the full memory array for one agent on demand (stripped from getTree). */
  getAgentMemory: (contextId: string, agentId: string) =>
    apiClient.get(`/contexts/${contextId}/agents/${agentId}/memory`),
  createZoneEvent: (data: {
    match_id: string;
    zone_map_context_id: string;
    title: string;
    description?: string;
    max_turns?: number;
    participant_ids?: string[];
  }) => apiClient.post('/contexts/zone-event', data),
};

export const entitiesApi = {
  list: (contextId: string) => apiClient.get(`/contexts/${contextId}/entities`),
  create: (data: {
    context_id: string;
    archetype: string;
    components?: Record<string, unknown>;
    tags?: string[];
  }) => apiClient.post('/entities', data),
};

export const commandsApi = {
  submit: (data: {
    match_id: string;
    context_id: string;
    command_type: string;
    payload?: Record<string, unknown>;
  }) => apiClient.post('/commands', data),
  list: (matchId: string) => apiClient.get(`/matches/${matchId}/commands`),
};

export const eventsApi = {
  listForMatch: (matchId: string, params?: { limit?: number }) =>
    apiClient.get(`/matches/${matchId}/events`, { params }),
  listForContext: (contextId: string, params?: { limit?: number }) =>
    apiClient.get(`/contexts/${contextId}/events`, { params }),
};

export const turnsApi = {
  getCurrent: (contextId: string) => apiClient.get(`/contexts/${contextId}/turn`),
  submit: (contextId: string) => apiClient.post(`/contexts/${contextId}/turn/submit`),
};

export const usersApi = {
  /** Admin: list all users */
  list: () => apiClient.get('/admin/users'),
  /** Admin: get user profile with stats */
  getProfile: (userId: string) => apiClient.get(`/admin/users/${userId}`),
  /** Admin: update user (toggle is_active / is_superuser) */
  update: (userId: string, data: { is_active?: boolean; is_superuser?: boolean }) =>
    apiClient.patch(`/admin/users/${userId}`, data),
  /** Admin: delete user */
  delete: (userId: string) => apiClient.delete(`/admin/users/${userId}`),
  /** Public: get any user's profile (needs auth) */
  publicProfile: (userId: string) => apiClient.get(`/admin/profile/${userId}`),
};

export const locationsApi = {
  /**
   * Upload an image for a zone-map location (legacy: uploads to 'clear' slot).
   * Returns a payload with the public URL and optional revision metadata.
   */
  uploadImage: (contextId: string, locationId: string, file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    return apiClient.post<{
      url: string;
      image_url?: string;
      location_id?: string;
      slot?: string;
      primary_image_slot?: string;
      image_slots?: Record<string, string | null>;
      state_revision?: number;
      map_revision?: number;
    }>(
      `/locations/${contextId}/${locationId}/image`,
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    );
  },

  /**
   * Upload an image for a specific slot of a zone-map location.
   */
  uploadImageSlot: (contextId: string, locationId: string, file: File, slot: string) => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('slot', slot);
    return apiClient.post<{
      url: string;
      image_url?: string;
      location_id?: string;
      slot?: string;
      primary_image_slot?: string;
      image_slots?: Record<string, string | null>;
      state_revision?: number;
      map_revision?: number;
    }>(
      `/locations/${contextId}/${locationId}/image`,
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    );
  },

  /** Delete the image attached to a location (all slots). */
  deleteImage: (contextId: string, locationId: string) =>
    apiClient.delete<{
      status: 'deleted';
      location_id: string;
      state_revision?: number;
      map_revision?: number;
    }>(`/locations/${contextId}/${locationId}/image`),

  /** Delete the image for a specific slot of a location. */
  deleteImageSlot: (contextId: string, locationId: string, slot: string) =>
    apiClient.delete<{
      status: 'deleted';
      location_id: string;
      slot: string;
      state_revision?: number;
      map_revision?: number;
    }>(`/locations/${contextId}/${locationId}/image`, { params: { slot } }),
};

export const zoneMapApi = {
  /** Fetch static map data (topology, names, terrain, connections). Cached by map_revision. */
  getMapStatic: (contextId: string) =>
    apiClient.get(`/zone-stalkers/contexts/${contextId}/map-static`),
  /** Fetch dynamic map data (agent positions, resource counts, anomaly activity). */
  getMapDynamic: (contextId: string) =>
    apiClient.get(`/zone-stalkers/contexts/${contextId}/map-dynamic`),
};

export const zoneDebugApi = {
  /** Get recent backend tick performance metrics for a match. */
  getPerformance: (matchId: string, params?: { limit?: number }) =>
    apiClient.get(`/zone-stalkers/debug/performance/${matchId}`, { params }),
  /** Get compact hunt_search_by_agent summary (all or filtered). */
  getHuntSearch: (contextId: string, params?: Record<string, unknown>) =>
    apiClient.get(`/zone-stalkers/contexts/${contextId}/debug/hunt-search`, { params }),
  /** Get full hunt_search data for a specific agent. */
  getHuntSearchAgent: (contextId: string, agentId: string, params?: Record<string, unknown>) =>
    apiClient.get(`/zone-stalkers/contexts/${contextId}/debug/hunt-search/agents/${agentId}`, { params }),
  /** Get hunt traces for a specific location. */
  getHuntSearchLocation: (contextId: string, locationId: string, params?: Record<string, unknown>) =>
    apiClient.get(`/zone-stalkers/contexts/${contextId}/debug/hunt-search/locations/${locationId}`, { params }),
  /** Get hunt search data for all hunters targeting a specific target. */
  getHuntSearchTarget: (contextId: string, targetId: string, params?: Record<string, unknown>) =>
    apiClient.get(`/zone-stalkers/contexts/${contextId}/debug/hunt-search/targets/${targetId}`, { params }),
  /** Trigger a rebuild of hunt debug payload for the context. */
  refreshHuntSearch: (contextId: string) =>
    apiClient.post(`/zone-stalkers/contexts/${contextId}/debug/hunt-search/refresh`),
};


export default apiClient;
