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
  linkedin_url?:    string | null
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
  deepen?:         boolean  // "Hunt deeper" re-run: widens the resolve slice server-side
}

export interface DuplicateContact {
  id:      number
  name:    string
  company: string
  email:   string
  status:  string
}

export interface HuntResult {
  contacts:       Partial<Contact>[]
  total:          number
  found?:         number   // leads discovered (pre-resolution)
  duplicates?:    number   // matches already in the user's list (skipped early or at save)
  role_filtered?: number   // reachable leads dropped by the role filter
  // Existing contacts that made leads duplicates (illustrative: deduped,
  // capped server-side — length may differ from `duplicates`).
  duplicate_contacts?: DuplicateContact[]
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

// Colors resolve through the CSS variables in styles/index.css so dark mode
// flips every pill/dot/funnel row automatically — never inline hexes here.
export const STATUS_META: Record<ContactStatus, { label: string; color: string; bg: string }> = {
  new:          { label: 'New',          color: 'var(--status-new)',          bg: 'var(--status-new-tint)'          },
  emailed:      { label: 'Emailed',      color: 'var(--status-emailed)',      bg: 'var(--status-emailed-tint)'      },
  followed_up:  { label: 'Followed up',  color: 'var(--status-followed_up)',  bg: 'var(--status-followed_up-tint)'  },
  replied:      { label: 'Replied',      color: 'var(--status-replied)',      bg: 'var(--status-replied-tint)'      },
  interview:    { label: 'Interview',    color: 'var(--status-interview)',    bg: 'var(--status-interview-tint)'    },
  offer:        { label: 'Offer',        color: 'var(--status-offer)',        bg: 'var(--status-offer-tint)'        },
  rejected:     { label: 'Rejected',     color: 'var(--status-rejected)',     bg: 'var(--status-rejected-tint)'     },
  bounced:      { label: 'Bounced',      color: 'var(--status-bounced)',      bg: 'var(--status-bounced-tint)'      },
}
