// Shared TypeScript types across the frontend

export type ContactStatus =
  | 'new'
  | 'emailed'
  | 'followed_up'
  | 'replied'
  | 'interview'
  | 'rejected'
  | 'bounced'

export interface Contact {
  id:               number
  name:             string
  email:            string
  designation:      string
  company:          string
  source:           string
  status:           ContactStatus
  notes?:           string
  last_emailed_at?: string | null
  replied_at?:      string | null
  bounced?:         boolean
  followups_sent?:  number
  email_status?:    'unknown' | 'valid' | 'risky' | 'invalid'
  created_at:       string
  updated_at:       string
}

export interface Draft {
  id:          number
  contact_id:  number
  subject:     string
  body:        string
  is_followup: boolean
  created_at:  string
}

export interface HuntRequest {
  query:           string
  hunter_api_key?: string
}

export interface HuntResult {
  contacts: Partial<Contact>[]
  total:    number
  sources:  Record<string, number>
}

export interface ComposeRequest {
  contact_id:       number
  resume:           string
  company_context?: string
}

export interface FollowUpRequest {
  contact_id:     number
  original_email: string
}

export const STATUS_META: Record<ContactStatus, { label: string; color: string; bg: string }> = {
  new:          { label: 'New',          color: '#64748b', bg: 'rgba(100,116,139,0.10)' },
  emailed:      { label: 'Emailed',      color: '#f59e0b', bg: 'rgba(245,158,11,0.10)'  },
  followed_up:  { label: 'Followed Up',  color: '#a78bfa', bg: 'rgba(167,139,250,0.10)' },
  replied:      { label: 'Replied',      color: '#22d3ee', bg: 'rgba(34,211,238,0.10)'  },
  interview:    { label: 'Interview',    color: '#34d399', bg: 'rgba(52,211,153,0.10)'  },
  rejected:     { label: 'Rejected',     color: '#ef4444', bg: 'rgba(239,68,68,0.10)'   },
  bounced:      { label: 'Bounced',      color: '#f87171', bg: 'rgba(248,113,113,0.10)' },
}
