import api from './client'

export interface VerifyResult {
  contact_id:   number
  email:        string
  email_status: 'valid' | 'risky' | 'invalid'
}

export interface VerifyResponse {
  valid:   number
  risky:   number
  invalid: number
  results: VerifyResult[]
}

export const verifyApi = {
  run: (contactIds: number[] = []) =>
    api.post<VerifyResponse>('/verify', { contact_ids: contactIds }).then(r => r.data),
}
