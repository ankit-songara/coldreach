import api from './client'

export interface SendResult {
  contact_id: number
  name:       string
  email:      string
  status:     'sent' | 'failed'
  error:      string
}

export interface BulkSendResponse {
  sent:     number
  failed:   number
  deferred: number
  results:  SendResult[]
}

export const sendApi = {
  bulk: (contactIds: number[], gmailAddress: string, gmailAppPassword: string) =>
    api.post<BulkSendResponse>('/send/bulk', {
      contact_ids:        contactIds,
      gmail_address:      gmailAddress,
      gmail_app_password: gmailAppPassword,
    }).then(r => r.data),

  test: (gmailAddress: string, gmailAppPassword: string) =>
    api.post<{ ok: boolean; message: string }>('/send/test', {
      contact_ids:        [],
      gmail_address:      gmailAddress,
      gmail_app_password: gmailAppPassword,
    }).then(r => r.data),

  schedule: (contactIds: number[], sendAtISO: string) =>
    api.post<{ scheduled: number; skipped: number }>('/send/schedule', {
      contact_ids: contactIds,
      send_at:     sendAtISO,
    }).then(r => r.data),
}
