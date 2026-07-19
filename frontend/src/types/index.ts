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
  status:           ContactStatus
  notes?:           string
  last_emailed_at?: string | null
  replied_at?:      string | null
  bounced?:         boolean
  followups_sent?:  number
  created_at:       string
  updated_at:       string
}

// Statuses meaning "we emailed this contact at some point" (first-touch delivered).
export const SENT_STATUSES: ContactStatus[] = ['emailed', 'followed_up', 'replied', 'interview', 'offer', 'rejected']

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
  role_filter?:    string   // target role family: '' = any (no filtering)
}

export interface HuntResult {
  contacts:       Partial<Contact>[]
  total:          number
  found?:         number   // leads discovered (pre-resolution)
  duplicates?:    number   // resolved contacts already in the user's list
  role_filtered?: number   // reachable leads dropped by the role filter
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
