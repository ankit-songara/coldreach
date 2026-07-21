import api from './client'
import type { HuntRequest, HuntResult } from '../types'

export const huntApi = {
  hunt: (req: HuntRequest, signal?: AbortSignal) =>
    api.post<HuntResult>('/hunt', req, { signal }).then(r => r.data),
  suggestions: () =>
    api.get<{ hiring_companies: string[]; hiring_now?: Array<{ name: string; role: string }> }>(
      '/hunt/suggestions',
    ).then(r => r.data),
}
