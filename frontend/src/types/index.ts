// Shared TypeScript types across the frontend

export type ContactStatus =
  | 'new'
  | 'emailed'
  | 'followed_up'
  | 'replied'
  | 'interview'
  | 'offer'
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
  confidence?:      number
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
  contacts:    Partial<Contact>[]
  total:       number
  sources:     Record<string, number>
  found?:      number   // leads discovered across sources (pre-resolution)
  duplicates?: number   // resolved contacts already in the user's list
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
  new:          { label: 'New',          color: '#8a7f70', bg: 'rgba(138,127,112,0.12)' },
  emailed:      { label: 'Emailed',      color: '#c47d1e', bg: 'rgba(196,125,30,0.13)'  },
  followed_up:  { label: 'Followed up',  color: '#6f5ae0', bg: 'rgba(111,90,224,0.12)'  },
  replied:      { label: 'Replied',      color: '#0e9d88', bg: 'rgba(14,157,136,0.13)'  },
  interview:    { label: 'Interview',    color: '#3f8f43', bg: 'rgba(63,143,67,0.13)'   },
  offer:        { label: 'Offer',        color: '#2f9e44', bg: 'rgba(47,158,68,0.16)'   },
  rejected:     { label: 'Rejected',     color: '#d2483a', bg: 'rgba(210,72,58,0.12)'   },
  bounced:      { label: 'Bounced',      color: '#cf6a59', bg: 'rgba(207,106,89,0.13)'  },
}
