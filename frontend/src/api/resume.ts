import api from './client'

export const resumeApi = {
  /** Most recently saved résumé for the current user (empty text if none). */
  getLatest: () =>
    api.get<{ text: string; filename: string | null }>('/resume/latest').then(r => r.data),

  /** Persist manually pasted/edited résumé text. */
  save: (text: string) =>
    api.post<{ text: string; filename: string | null }>('/resume/save', { text }).then(r => r.data),
}
