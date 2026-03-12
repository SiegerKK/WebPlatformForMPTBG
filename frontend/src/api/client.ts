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
  create: (data: { game_id: string; config?: Record<string, unknown> }) =>
    apiClient.post('/matches', data),
  get: (id: string) => apiClient.get(`/matches/${id}`),
  join: (id: string) => apiClient.post(`/matches/${id}/join`),
  start: (id: string) => apiClient.post(`/matches/${id}/start`),
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
  listForMatch: (matchId: string) => apiClient.get(`/matches/${matchId}/events`),
  listForContext: (contextId: string) => apiClient.get(`/contexts/${contextId}/events`),
};

export const turnsApi = {
  getCurrent: (contextId: string) => apiClient.get(`/contexts/${contextId}/turn`),
  submit: (contextId: string) => apiClient.post(`/contexts/${contextId}/turn/submit`),
};

export default apiClient;
