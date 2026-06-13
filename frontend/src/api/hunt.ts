import api from './client'
import type { HuntRequest, HuntResult } from '../types'

export const huntApi = {
  hunt: (req: HuntRequest) => api.post<HuntResult>('/hunt', req).then(r => r.data),
}
