import api from './client'

export interface AuthResponse {
  token:   string
  email:   string
  user_id: number
}

export const authApi = {
  register: (email: string, password: string) =>
    api.post<AuthResponse>('/auth/register', { email, password }).then(r => r.data),

  login: (email: string, password: string) =>
    api.post<AuthResponse>('/auth/login', { email, password }).then(r => r.data),

  google: (credential: string) =>
    api.post<AuthResponse>('/auth/google', { credential }).then(r => r.data),

  me: () =>
    api.get<{ id: number; email: string }>('/auth/me').then(r => r.data),

  logout: () =>
    api.post('/auth/logout').then(r => r.data).catch(() => undefined),
}
