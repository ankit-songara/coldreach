import api from './client'
import type { HuntRequest, HuntResult } from '../types'

export const huntApi = {
  // The hunt is the one endpoint that legitimately runs to the backend's 60s
  // Vercel wall, and a cold start adds several seconds ON TOP before the handler
  // even starts — so the global 65s cap could fire on a slow-but-SUCCESSFUL
  // broad hunt (the backend returns 200 and the contacts are already saved, yet
  // the browser had already given up with "request timed out"). Give this call
  // its own generous 90s budget so those results actually land.
  hunt: (req: HuntRequest, signal?: AbortSignal) =>
    api.post<HuntResult>('/hunt', req, { signal, timeout: 90_000 }).then(r => r.data),
  suggestions: () =>
    api.get<{ hiring_companies: string[]; hiring_now?: Array<{ name: string; role: string }> }>(
      '/hunt/suggestions',
    ).then(r => r.data),
}
