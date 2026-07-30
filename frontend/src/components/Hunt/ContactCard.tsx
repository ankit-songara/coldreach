import { memo } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { X, Check, Linkedin } from 'lucide-react'
import { contactsApi } from '../../api/contacts'
import { useStore } from '../../store'
import { STATUS_META, type Contact, type ContactStatus } from '../../types'
import { contactDisplayName, isGenericName, displayDesignation } from '../../lib/display'

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

// Hoisted so the 8 status buttons aren't rebuilt from scratch for every card on
// every render — compounds hard at 245 cards during background hunt-stage ticks.
const STATUS_ENTRIES = Object.entries(STATUS_META) as [ContactStatus, typeof STATUS_META[ContactStatus]][]

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
  const upsertContact   = useStore(s => s.upsertContact)
  const removeContact   = useStore(s => s.removeContact)
  const removeHuntResult = useStore(s => s.removeHuntResult)
  const updateHuntResult = useStore(s => s.updateHuntResult)
  const qc = useQueryClient()

  // Cards can render from the hunt-results list (fresh hunt) OR the saved
  // contacts list — both must be updated or the change looks like it failed.
  const statusMutation = useMutation({
    mutationFn: (status: ContactStatus) => contactsApi.setStatus(c.id, status),
    onSuccess: (updated) => {
      upsertContact(updated)
      updateHuntResult(updated)
      // Patch the shared query cache too (deleteMutation below already does).
      // Without it the cached list still holds the OLD status, so the next
      // refetch/mirror into the store silently reverted the change on screen.
      qc.setQueryData<Contact[]>(['contacts'], rows =>
        rows?.map(x => (x.id === updated.id ? updated : x)))
      qc.invalidateQueries({ queryKey: ['analytics'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

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
          className="relative w-5 h-5 flex items-center justify-center rounded before:absolute before:-inset-2.5 before:content-['']"
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

      {/* ── Company + email ── */}
      <div className="text-xs mb-1" style={{ color: generic ? 'var(--text)' : 'var(--text-muted)', fontWeight: generic ? 600 : 400 }}>🏢 {c.company}</div>
      <div className="flex items-center gap-1.5 mb-3 flex-wrap">
        <span className="text-xs font-mono truncate" style={{ color: 'var(--text-muted)' }}>{c.email}</span>
        {/* Only when the verifier actually confirmed the address — never decorative. */}
        {verified && (
          <span
            className="badge flex-shrink-0"
            style={{
              background: 'color-mix(in srgb, var(--success) 12%, transparent)',
              color: 'var(--success-text)',
              fontSize: 10,
            }}
          >
            ✓ Verified email
          </span>
        )}
        {c.linkedin_url && (
          <a
            href={c.linkedin_url}
            target="_blank"
            rel="noopener noreferrer nofollow"
            onClick={e => e.stopPropagation()}
            aria-label="Open LinkedIn profile"
            title="LinkedIn profile"
            className="hit-target inline-flex items-center flex-shrink-0"
            style={{ color: 'var(--accent-text)' }}
          >
            <Linkedin size={13} />
          </a>
        )}
      </div>
      {/* ── Status pills ── */}
      <div className="flex flex-wrap gap-1">
        {STATUS_ENTRIES.map(([key, meta]) => (
          <button
            key={key}
            // In select mode the whole card is a selection target, so a tap
            // landing on a pill must ONLY toggle selection — it used to also
            // fire a silent status PATCH (e.g. marking someone "Offer") that
            // then polluted the funnel.
            onClick={selectable ? undefined : () => statusMutation.mutate(key)}
            disabled={statusMutation.isPending || selectable}
            tabIndex={selectable ? -1 : undefined}
            className="relative text-xs px-2 py-0.5 rounded-full font-bold font-mono transition-colors before:absolute before:-inset-y-2.5 before:inset-x-0 before:content-['']"
            style={{
              background: c.status === key ? meta.bg    : 'transparent',
              color:      c.status === key ? meta.color : 'var(--text-muted)',
              border:     `1px solid ${c.status === key ? `color-mix(in srgb, ${meta.color} 31%, transparent)` : 'var(--border)'}`,
            }}
          >
            {meta.label}
          </button>
        ))}
      </div>
    </div>
  )
}

// Memoized: with stable id-based callbacks from the parent + action-only store
// selectors above, a card re-renders only when ITS contact/selected/selectable
// change — not when any other contact or unrelated store slice changes.
export default memo(ContactCard)
