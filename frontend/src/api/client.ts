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
   * Upload an image for a zone-map location.
   * Returns `{ url: string }` — the public path to the uploaded image.
   */
  uploadImage: (contextId: string, locationId: string, file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    return apiClient.post<{ url: string }>(
      `/locations/${contextId}/${locationId}/image`,
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    );
  },

  /** Delete the image attached to a location. */
  deleteImage: (contextId: string, locationId: string) =>
    apiClient.delete(`/locations/${contextId}/${locationId}/image`),
};

export default apiClient;
