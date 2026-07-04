import api from './client'

export interface ConfigStatus {
  sender_name:     string
  signature_links: string
}

export const automationApi = {
  getConfig: () =>
    api.get<ConfigStatus>('/config').then(r => r.data),

  setProfile: (senderName: string, signatureLinks?: string) =>
    api.post<ConfigStatus>('/config/profile', {
      sender_name: senderName, signature_links: signatureLinks,
    }).then(r => r.data),
}
