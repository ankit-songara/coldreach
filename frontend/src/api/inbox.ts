import api from './client'

export interface ReplyHit {
  contact_id: number
  name:       string
  email:      string
}

export interface InboxSyncResponse {
  scanned:             number
  replies_found:       number
  bounces_found:       number
  followups_cancelled: number
  hits:                ReplyHit[]
}

export const inboxApi = {
  sync: (gmailAddress: string, gmailAppPassword: string) =>
    api.post<InboxSyncResponse>('/inbox/sync', {
      gmail_address:      gmailAddress,
      gmail_app_password: gmailAppPassword,
    }).then(r => r.data),
}
