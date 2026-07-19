import api from './client'

export interface ConfigStatus {
  sender_name:     string
  signature_links: string
  gmail_address:   string   // stored sending address ('' = not connected)
  has_gmail:       boolean  // creds stored server-side (encrypted)
  gmail_method:    '' | 'oauth' | 'app_password'  // how Gmail is connected ('' = not connected)
  oauth_available: boolean  // server has Google OAuth configured
}

export const automationApi = {
  getConfig: () =>
    api.get<ConfigStatus>('/config').then(r => r.data),

  setProfile: (senderName: string, signatureLinks?: string) =>
    api.post<ConfigStatus>('/config/profile', {
      sender_name: senderName, signature_links: signatureLinks,
    }).then(r => r.data),

  // Verifies against Gmail, then stores (password encrypted server-side).
  saveGmail: (gmailAddress: string, gmailAppPassword: string) =>
    api.post<ConfigStatus>('/config/gmail', {
      gmail_address: gmailAddress, gmail_app_password: gmailAppPassword,
    }).then(r => r.data),

  deleteGmail: () =>
    api.delete<ConfigStatus>('/config/gmail').then(r => r.data),

  // Returns the Google consent URL — the browser should be redirected there
  // (full page, not a fetch): window.location.href = url.
  gmailOauthStart: () =>
    api.get<{ url: string }>('/config/gmail/oauth/start').then(r => r.data),

  gmailOauthDisconnect: () =>
    api.delete<ConfigStatus>('/config/gmail/oauth').then(r => r.data),
}
