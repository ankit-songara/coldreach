import api from './client'

export const demoApi = {
  /** Seed a realistic sample pipeline for the current user (no-op if present). */
  seed: () =>
    api.post<{ seeded: boolean; contacts?: number; drafts?: number; message?: string }>('/demo/seed')
      .then(r => r.data),

  /** Remove everything the seeder created. */
  clear: () =>
    api.delete<{ cleared: number }>('/demo').then(r => r.data),
}

/** Sentinel stored in Contact.notes so the UI can detect demo data. */
export const DEMO_SENTINEL = '__seed_demo__'
