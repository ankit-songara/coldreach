import api from './client'
import type { ContactStatus } from '../types'

export interface ReplyHit {
  contact_id: number
  name:       string
  email:      string
}

export interface InboxSyncResponse {
  scanned:       number
  replies_found: number
  bounces_found: number
  hits:          ReplyHit[]
}

// A stored reply captured by /inbox/sync, joined with the contact's current
// identity/status — powers the Replies inbox view. NOTE: no email field here;
// the UI must never invent one.
export interface ReplyMessage {
  id:          number
  contact_id:  number
  name:        string
  company:     string
  designation: string
  status:      ContactStatus
  subject:     string
  snippet:     string
  received_at: string | null
}

export const inboxApi = {
  sync: (gmailAddress: string, gmailAppPassword: string) =>
    api.post<InboxSyncResponse>('/inbox/sync', {
      gmail_address:      gmailAddress,
      gmail_app_password: gmailAppPassword,
    }).then(r => r.data),

  // Newest-first, capped at 100 server-side.
  replies: () => api.get<ReplyMessage[]>('/inbox/replies').then(r => r.data),
}
