import api from './client'
import type { Draft, ComposeRequest, FollowUpRequest } from '../types'

export const composeApi = {
  generate:   (req: ComposeRequest)   => api.post<Draft>('/compose', req).then(r => r.data),
  followUp:   (req: FollowUpRequest)  => api.post<Draft>('/compose/followup', req).then(r => r.data),
  getDrafts:  (contactId: number)     => api.get<Draft[]>(`/compose/${contactId}`).then(r => r.data),
  // One request for every draft the user has — grouped client-side by contact.
  getAllDrafts: ()                    => api.get<Draft[]>('/compose/drafts/all').then(r => r.data),
  editDraft:  (draftId: number, subject: string, body: string) =>
    api.put<Draft>(`/compose/draft/${draftId}`, { subject, body }).then(r => r.data),
}
