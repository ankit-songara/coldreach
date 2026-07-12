import api from './client'
import type { HuntRequest, HuntResult } from '../types'

export const huntApi = {
  hunt: (req: HuntRequest, signal?: AbortSignal) =>
    api.post<HuntResult>('/hunt', req, { signal }).then(r => r.data),
}
