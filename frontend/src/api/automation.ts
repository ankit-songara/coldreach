import api from './client'

export interface ConfigStatus {
  gmail_address:      string
  has_credentials:    boolean
  automation_enabled: boolean
  daily_send_cap:     number
}

export interface ScheduledItem {
  id:          number
  contact_id:  number
  name:        string
  email:       string
  subject:     string
  send_at:     string
  is_followup: boolean
}

export interface ScheduleFollowupsResponse {
  scheduled: number
  skipped:   number
  items:     ScheduledItem[]
}

export const automationApi = {
  getConfig: () =>
    api.get<ConfigStatus>('/config').then(r => r.data),

  saveGmail: (gmailAddress: string, gmailAppPassword: string) =>
    api.post<ConfigStatus>('/config/gmail', {
      gmail_address: gmailAddress, gmail_app_password: gmailAppPassword,
    }).then(r => r.data),

  setAutomation: (opts: { enabled?: boolean; daily_send_cap?: number }) =>
    api.post<ConfigStatus>('/config/automation', opts).then(r => r.data),

  scheduleFollowups: (contactIds: number[], days: number) =>
    api.post<ScheduleFollowupsResponse>('/followups/schedule', {
      contact_ids: contactIds, days,
    }).then(r => r.data),

  listFollowups: () =>
    api.get<ScheduledItem[]>('/followups').then(r => r.data),

  cancelFollowup: (id: number) =>
    api.delete(`/followups/${id}`).then(r => r.data),
}
