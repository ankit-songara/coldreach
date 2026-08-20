import { memo } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { X, Check, Linkedin, Globe, Target } from 'lucide-react'
import { contactsApi } from '../../api/contacts'
import { useStore } from '../../store'
import { STATUS_META, type Contact } from '../../types'
import {
  contactDisplayName, isGenericName, displayDesignation,
  leadRole, leadSource, companyWebsite, timeAgo,
} from '../../lib/display'

// The API still sends email_status; the shared Contact type dropped it, so
// it's typed locally and the chip simply doesn't render when it's absent.
type ContactMeta = Contact & { email_status?: string }

// Color only — the designation text itself says who this is. Exported for
// the contact drawer so both render the same avatar tint.
export function getDesigColor(d: string): string {
  const dl = d.toLowerCase()
  if (['founder', 'co-founder', 'ceo', 'cto', 'chief', 'founding'].some(x => dl.includes(x)))
    return 'var(--tier-founder)'
  if (['hr', 'human resource', 'talent', 'recruiter', 'recruiting', 'people ops', 'people partner'].some(x => dl.includes(x)))
    return 'var(--tier-hr)'
  if (['engineer', 'developer', 'swe', 'software', 'backend', 'frontend', 'fullstack', 'devops', 'data'].some(x => dl.includes(x)))
    return 'var(--tier-engineer)'
  return 'var(--tier-default)'
}

interface Props {
  contact: Contact
  selectable?: boolean
  selected?: boolean
  // Take the id so the parent can pass ONE stable callback for all cards
  // (inline `() => fn(c.id)` closures would defeat React.memo).
  onToggleSelect?: (id: number) => void
  onOpenDetails?: (id: number) => void
}

function ContactCard({ contact: c, selectable, selected, onToggleSelect, onOpenDetails }: Props) {
  // Action-only selectors: Zustand action refs are stable, so this card never
  // re-renders from a store DATA change — only when its own props change (paired
  // with React.memo below). Was `useStore()` with no selector, which subscribed
  // every card to the WHOLE store → all 245 re-rendered on any write.
  const removeContact   = useStore(s => s.removeContact)
  const removeHuntResult = useStore(s => s.removeHuntResult)
  const qc = useQueryClient()

  const deleteMutation = useMutation({
    mutationFn: () => contactsApi.delete(c.id),
    onSuccess: () => {
      removeContact(c.id)
      removeHuntResult(c.id)
      qc.setQueryData<Contact[]>(['contacts'], rows => rows?.filter(x => x.id !== c.id))
      toast('Contact removed', { icon: '🗑️' })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const displayName = contactDisplayName(c)
  const generic = isGenericName(c.name)
  const initials = displayName.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase()
  const desigColor = getDesigColor(c.designation)
  const verified = (c as ContactMeta).email_status === 'valid'

  // ── Trust line + links (surface what the hunt already extracted) ──────────
  const role    = leadRole(c.context)         // the concrete open role, if any
  const via     = leadSource(c.source)        // which board/site found them
  const found   = timeAgo(c.created_at)       // freshness: "found 2d ago"
  const website = companyWebsite(c.email)     // company site from the domain
  // Reachability only when it's a genuinely positive signal — the many real
  // direct-scraped emails carry confidence 0, so showing "0%" would undersell.
  const reachable = (c.confidence ?? 0) >= 50 ? c.confidence : null
  const status = STATUS_META[c.status]

  // The card body opens the detail drawer as a pointer convenience — clicks
  // that land on any nested control (status pills, delete ×) are ignored so
  // recording an outcome never accidentally opens the drawer. The card itself
  // carries no button role: it nests real buttons, and nested interactive
  // elements are invalid. The keyboard/AT path is the header region below,
  // which contains no interactive children and so can be a proper button.
  const handleCardClick = (e: ReactMouseEvent) => {
    if (selectable) { onToggleSelect?.(c.id); return }
    if ((e.target as HTMLElement).closest('button, [role="button"]')) return
    onOpenDetails?.(c.id)
  }

  return (
    <div
      className="card relative group cv-card"
      style={{
        transition: 'border-color .15s',
        cursor: selectable || onOpenDetails ? 'pointer' : undefined,
        ...(selected ? { borderColor: 'var(--accent)', boxShadow: 'var(--glow-accent)' } : {}),
      }}
      onClick={selectable || onOpenDetails ? handleCardClick : undefined}
    >
      {/* ── Top-right controls ── */}
      <div className="absolute top-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 cr-reveal transition-opacity">
        <button
          onClick={e => { e.stopPropagation(); deleteMutation.mutate() }}
          title="Remove"
          aria-label="Remove contact"
          className="relative w-5 h-5 flex items-center justify-center rounded before:absolute before:-inset-3 before:content-['']"
          style={{ background: 'color-mix(in srgb, var(--danger) 8%, transparent)', color: 'var(--text-muted)' }}
        >
          <X size={10} />
        </button>
      </div>

      {/* ── Checkbox (bulk select mode) ── */}
      {selectable && (
        <div className="absolute top-2 left-2">
          <div
            className="w-5 h-5 rounded flex items-center justify-center transition-colors"
            style={{
              border: `2px solid ${selected ? 'var(--accent)' : 'var(--border-strong)'}`,
              background: selected ? 'var(--accent)' : 'transparent',
            }}
          >
            {selected && <Check size={12} color="var(--on-accent)" strokeWidth={3} />}
          </div>
        </div>
      )}

      {/* ── Avatar + name — the accessible "details" affordance. This region
          nests no interactive elements, so it can safely be a button for
          keyboard/screen-reader users (the whole card is pointer-clickable). */}
      <div
        className="flex items-center gap-3 mb-3 pr-6"
        style={selectable ? { paddingLeft: 24 } : undefined}
        {...(onOpenDetails && !selectable ? {
          role: 'button' as const,
          tabIndex: 0,
          'aria-label': `View details for ${displayName}`,
          onKeyDown: (e: ReactKeyboardEvent) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpenDetails(c.id) }
          },
          // Must handle clicks itself: the card-level handler ignores clicks
          // inside any [role="button"] — which includes THIS region — so
          // without this, clicking the name/avatar (the most natural target)
          // would do nothing.
          onClick: (e: ReactMouseEvent) => { e.stopPropagation(); onOpenDetails(c.id) },
        } : {})}
      >
        <div
          className="w-9 h-9 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0"
          style={{
            background: `color-mix(in srgb, ${desigColor} 9%, transparent)`,
            color: desigColor,
            border: `1.5px solid color-mix(in srgb, ${desigColor} 19%, transparent)`,
          }}
        >
          {initials}
        </div>
        <div className="min-w-0">
          <div className="text-sm font-medium truncate">{displayName}</div>
          <span
            className="badge"
            style={{
              background: `color-mix(in srgb, ${desigColor} 9%, transparent)`,
              color: desigColor,
              fontSize: '11px',
              marginTop: '2px',
            }}
          >
            {displayDesignation(c.designation)}
          </span>
        </div>
      </div>

      {/* ── Company + outbound links ── */}
      <div className="flex items-center gap-2 mb-1">
        <span
          className="text-xs truncate"
          style={{ color: generic ? 'var(--text)' : 'var(--text-muted)', fontWeight: generic ? 600 : 400 }}
        >🏢 {c.company}</span>
        {website && (
          <a
            href={website}
            target="_blank" rel="noopener noreferrer nofollow"
            onClick={e => e.stopPropagation()}
            // Label by the DESTINATION domain, not c.company: the URL is
            // derived from the email domain, which can be an agency/parent
            // (jane@talent-partners.com) different from the company shown —
            // announcing "Open Acme website" for talent-partners.com misleads.
            aria-label={`Open ${website.replace(/^https?:\/\//, '')}`} title={website.replace(/^https?:\/\//, '')}
            className="hit-target inline-flex items-center flex-shrink-0"
            style={{ color: 'var(--text-muted)' }}
          >
            <Globe size={13} />
          </a>
        )}
        {c.linkedin_url && (
          <a
            href={c.linkedin_url}
            target="_blank" rel="noopener noreferrer nofollow"
            onClick={e => e.stopPropagation()}
            aria-label="Open LinkedIn profile" title="LinkedIn profile"
            className="hit-target inline-flex items-center flex-shrink-0"
            style={{ color: 'var(--accent-text)' }}
          >
            <Linkedin size={13} />
          </a>
        )}
      </div>

      {/* ── Email + verification + reachability number ── */}
      <div className="flex items-center gap-1.5 mb-2 flex-wrap">
        <span className="text-xs font-mono truncate" style={{ color: 'var(--text-muted)' }}>{c.email}</span>
        {verified && (
          <span
            className="badge flex-shrink-0"
            style={{ background: 'color-mix(in srgb, var(--success) 12%, transparent)', color: 'var(--success-text)', fontSize: 10 }}
          >✓ verified</span>
        )}
        {reachable != null && (
          <span
            className="badge flex-shrink-0 tnum"
            title="How likely this address reaches a real person"
            style={{ background: 'var(--surface-2)', color: 'var(--text-dim)', fontSize: 10 }}
          >{reachable}% reachable</span>
        )}
      </div>

      {/* ── Trust line: why this is a real lead + how fresh ── */}
      {(role || via || found) && (
        <div
          className="text-[11px] mb-2.5 flex items-center gap-1 flex-wrap"
          style={{ color: 'var(--text-dim)' }}
        >
          {role && (
            <span className="inline-flex items-center gap-1 min-w-0">
              <Target size={11} className="flex-shrink-0" />
              <span className="truncate">Hiring: <span style={{ color: 'var(--text-muted)', fontWeight: 500 }}>{role}</span></span>
            </span>
          )}
          {via && <span className="flex-shrink-0">{role ? '· ' : ''}via {via}</span>}
          {found && <span className="tnum flex-shrink-0">{(role || via) ? '· ' : ''}found {found}</span>}
        </div>
      )}

      {/* ── Current pipeline status (change it in the detail drawer) ── */}
      <div className="flex items-center gap-1.5">
        <span
          className="badge"
          title="Pipeline status — open the card to change it"
          style={{
            background: status.bg, color: status.color, fontSize: 11, fontWeight: 700,
            border: `1px solid color-mix(in srgb, ${status.color} 31%, transparent)`,
          }}
        >
          <span aria-hidden style={{ marginRight: 5 }}>●</span>{status.label}
        </span>
      </div>
    </div>
  )
}

// Memoized: with stable id-based callbacks from the parent + action-only store
// selectors above, a card re-renders only when ITS contact/selected/selectable
// change — not when any other contact or unrelated store slice changes.
export default memo(ContactCard)
