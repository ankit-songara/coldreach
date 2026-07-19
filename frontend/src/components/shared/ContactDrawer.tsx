import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { X, Copy, ExternalLink } from 'lucide-react'
import { useStore } from '../../store'
import { contactsApi } from '../../api/contacts'
import { STATUS_META, type Contact, type ContactStatus } from '../../types'
import { contactDisplayName } from '../../lib/display'
import { getDesigColor } from '../Hunt/ContactCard'

// The API still returns these fields; the shared Contact type dropped them.
// Typed locally so we never *rely* on their presence.
type ContactMeta = Contact & { email_status?: string; source?: string }

// Outcomes a user records by hand — same set as Send's OUTCOME row.
const OUTCOME_STEPS: ContactStatus[] = ['replied', 'interview', 'offer', 'rejected']

// Backend timestamps are naive UTC (no zone suffix) — parse them as UTC, not
// local time, or "2h ago" would be off by the viewer's UTC offset.
function parseUTC(iso: string): Date {
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')
}

function timeAgo(d: Date): string {
  const s = (Date.now() - d.getTime()) / 1000
  if (!Number.isFinite(s)) return ''
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

function absolute(d: Date): string {
  return `${d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}, ${
    d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
}

interface TimelineEvent {
  key:   string
  title: string
  when:  Date
  dot:   string
}

// Real history only — a null field means the event never happened, so no row.
function buildTimeline(c: ContactMeta, drafts: Record<number, { id: number; is_followup: boolean; created_at: string }[]>): TimelineEvent[] {
  const events: TimelineEvent[] = []
  if (c.created_at) {
    events.push({
      key: 'found',
      title: `Found via ${c.source || 'hunt'}`,
      when: parseUTC(c.created_at),
      dot: 'var(--accent)',
    })
  }
  for (const d of drafts[c.id] ?? []) {
    events.push({
      key: `draft-${d.id}`,
      title: d.is_followup ? 'Follow-up drafted' : 'Draft generated',
      when: parseUTC(d.created_at),
      dot: 'var(--info)',
    })
  }
  if (c.last_emailed_at) {
    events.push({
      key: 'emailed',
      title: 'First email sent',
      when: parseUTC(c.last_emailed_at),
      dot: 'var(--status-emailed)',
    })
  }
  if (c.replied_at) {
    events.push({
      key: 'replied',
      title: 'They replied',
      when: parseUTC(c.replied_at),
      dot: 'var(--status-replied)',
    })
  }
  return events.sort((a, b) => b.when.getTime() - a.when.getTime())   // newest first
}

interface Props {
  contact: Contact | null
  onClose: () => void
}

/**
 * Slide-in contact detail drawer. Renders nothing when `contact` is null.
 *
 * A11y: moves focus to the close button on open, traps Tab inside, closes on
 * Escape / scrim click, and returns focus to the opener on close (same
 * pattern as ConfirmDialog). Keyed by contact id so notes state resets when
 * the drawer switches contacts.
 */
export default function ContactDrawer({ contact, onClose }: Props) {
  if (!contact) return null
  return <DrawerPanel key={contact.id} contact={contact} onClose={onClose} />
}

function DrawerPanel({ contact: c, onClose }: { contact: Contact; onClose: () => void }) {
  const meta = c as ContactMeta
  const { drafts, upsertContact, updateHuntResult } = useStore()
  const qc = useQueryClient()
  const panelRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const [notes, setNotes] = useState(c.notes ?? '')
  const [savingNote, setSavingNote] = useState(false)

  // Remember what was focused before the drawer opened, focus the close
  // button on mount, and hand focus back when the drawer unmounts.
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    closeRef.current?.focus()
    return () => opener?.focus()
  }, [])

  // Escape closes; Tab is trapped inside the panel (ConfirmDialog's pattern).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { onClose(); return }
      if (e.key !== 'Tab') return
      const root = panelRef.current
      if (!root) return
      const focusables = Array.from(
        root.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        )
      ).filter(el => !el.hasAttribute('disabled'))
      if (focusables.length === 0) { e.preventDefault(); return }
      const first = focusables[0]
      const last = focusables[focusables.length - 1]
      const active = document.activeElement
      const inside = active instanceof HTMLElement && root.contains(active)
      if (e.shiftKey) {
        if (!inside || active === first) { e.preventDefault(); last.focus() }
      } else {
        if (!inside || active === last) { e.preventDefault(); first.focus() }
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  // Optimistic status change — same pattern as Send: the pill flips
  // instantly, rolled back if the request fails. Hunt-result rows are
  // mirrored too, so a card behind the drawer never shows a stale status.
  const statusMutation = useMutation({
    mutationFn: (status: ContactStatus) => contactsApi.setStatus(c.id, status),
    onMutate: (status) => {
      const prev = useStore.getState().contacts.find(x => x.id === c.id) ?? c
      upsertContact({ ...prev, status })
      updateHuntResult({ ...prev, status })
      return { prev }
    },
    onSuccess: (updated) => {
      upsertContact(updated)
      updateHuntResult(updated)
      qc.invalidateQueries({ queryKey: ['contacts'] })
    },
    onError: (e: Error, _status, ctx) => {
      if (ctx?.prev) { upsertContact(ctx.prev); updateHuntResult(ctx.prev) }
      toast.error(e.message)
    },
  })

  const copyEmail = async () => {
    try {
      await navigator.clipboard.writeText(c.email)
      toast.success('Email address copied')
    } catch {
      toast.error("Couldn't copy — select the address manually")
    }
  }

  const saveNote = async () => {
    setSavingNote(true)
    try {
      const updated = await contactsApi.update(c.id, { notes })
      upsertContact(updated)
      updateHuntResult(updated)
      qc.invalidateQueries({ queryKey: ['contacts'] })
      toast.success('Note saved')
    } catch (e: any) {
      toast.error(e.message)
    } finally {
      setSavingNote(false)
    }
  }

  const openGmail = () => {
    window.open(`https://mail.google.com/mail/?view=cm&to=${encodeURIComponent(c.email)}`, '_blank')
  }

  const displayName = contactDisplayName(c)
  const initials = displayName.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase()
  const desigColor = getDesigColor(c.designation)
  const verified = meta.email_status === 'valid'
  const timeline = buildTimeline(meta, drafts)
  const noteDirty = notes !== (c.notes ?? '')

  return (
    <>
      {/* Slide-in + responsive width can't come from inline styles; the app
          stylesheet is off-limits here so the drawer carries its own rules.
          prefers-reduced-motion is neutralised globally in index.css. */}
      <style>{`
        @keyframes cr-drawer-in { from { transform: translateX(40px); opacity: 0; } to { transform: none; opacity: 1; } }
        .cr-drawer { width: 380px; }
        @media (max-width: 480px) { .cr-drawer { width: 100%; } }
      `}</style>

      {/* Scrim — click closes */}
      <div
        className="fixed inset-0"
        style={{ background: 'rgba(0, 0, 0, 0.4)', zIndex: 60 }}
        onClick={onClose}
        aria-hidden
      />

      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={`Details for ${displayName}`}
        className="cr-drawer fixed top-0 right-0 bottom-0 overflow-y-auto"
        style={{
          zIndex: 61,
          background: 'var(--surface-1)',
          borderLeft: '1px solid var(--border)',
          boxShadow: 'var(--shadow-lg)',
          padding: '22px 24px',
          animation: 'cr-drawer-in .25s var(--ease-out) both',
        }}
      >
        {/* ── Header ── */}
        <div className="flex items-center gap-3">
          <span
            className="w-10 h-10 rounded-full flex items-center justify-center text-xs font-extrabold flex-shrink-0"
            style={{
              background: `color-mix(in srgb, ${desigColor} 9%, transparent)`,
              color: desigColor,
              border: `1.5px solid color-mix(in srgb, ${desigColor} 19%, transparent)`,
            }}
            aria-hidden
          >
            {initials}
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-base font-bold truncate">{displayName}</div>
            <div className="text-xs truncate" style={{ color: 'var(--text-muted)' }}>
              {c.designation} · {c.company}
            </div>
          </div>
          <button
            ref={closeRef}
            onClick={onClose}
            aria-label="Close contact details"
            className="hit-target w-[30px] h-[30px] flex items-center justify-center rounded-[9px] flex-shrink-0"
            style={{
              background: 'var(--surface-2)',
              border: '1px solid var(--border)',
              color: 'var(--text-muted)',
              cursor: 'pointer',
            }}
          >
            <X size={14} />
          </button>
        </div>

        {/* ── Email line ── */}
        <div className="flex items-center gap-1.5 flex-wrap mt-2.5 mb-5">
          <span className="text-xs font-mono truncate" style={{ color: 'var(--text-muted)' }}>{c.email}</span>
          <button
            onClick={copyEmail}
            aria-label="Copy email address"
            title="Copy email address"
            className="hit-target w-5 h-5 flex items-center justify-center rounded flex-shrink-0"
            style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer' }}
          >
            <Copy size={12} />
          </button>
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
        </div>

        {/* ── Timeline ── */}
        <div className="text-[10px] font-mono font-bold tracking-widest mb-2.5" style={{ color: 'var(--text-muted)' }}>
          TIMELINE
        </div>
        <div
          className="flex flex-col gap-3.5 mb-6"
          style={{ borderLeft: '2px solid var(--border)', paddingLeft: 16, marginLeft: 4 }}
        >
          {timeline.map(ev => (
            <div key={ev.key} className="relative">
              <span
                className="absolute rounded-full"
                style={{ left: -22, top: 4, width: 10, height: 10, background: ev.dot, border: '2px solid var(--surface-1)' }}
                aria-hidden
              />
              <div className="text-xs font-bold">{ev.title}</div>
              <div className="text-[10px] font-mono tnum" style={{ color: 'var(--text-dim)', marginTop: 1 }}>
                {timeAgo(ev.when)} · {absolute(ev.when)}
              </div>
            </div>
          ))}
        </div>

        {/* ── Notes ── */}
        <label
          htmlFor="cr-drawer-notes"
          className="block text-[10px] font-mono font-bold tracking-widest mb-2"
          style={{ color: 'var(--text-muted)' }}
        >
          NOTES
        </label>
        <textarea
          id="cr-drawer-notes"
          className="input"
          style={{ minHeight: 74, resize: 'vertical', fontSize: 13 }}
          maxLength={2000}
          placeholder="Met at PyCon volunteer booth… referred by Sam…"
          value={notes}
          onChange={e => setNotes(e.target.value)}
        />
        <button
          onClick={saveNote}
          disabled={savingNote || !noteDirty}
          className="btn btn-ghost text-xs mt-2"
          style={{ opacity: savingNote || !noteDirty ? 0.5 : 1 }}
        >
          {savingNote ? 'Saving…' : 'Save note'}
        </button>

        {/* ── Quick actions ── */}
        <div className="mt-5 pt-4" style={{ borderTop: '1px solid var(--border)' }}>
          <div className="text-[10px] font-mono font-bold tracking-widest mb-2.5" style={{ color: 'var(--text-muted)' }}>
            OUTCOME
          </div>
          <div className="flex items-center gap-1.5 flex-wrap">
            {OUTCOME_STEPS.map(s => {
              const stMeta = STATUS_META[s]
              const active = c.status === s
              return (
                <button
                  key={s}
                  onClick={() => statusMutation.mutate(s)}
                  className="text-[11px] px-2 py-0.5 rounded-full font-semibold transition-all hit-target"
                  style={{
                    background: active ? stMeta.bg : 'transparent',
                    // stMeta.color is a var() — alpha via color-mix, never string-suffix tricks
                    color:      active ? stMeta.color : 'var(--text-muted)',
                    border:     `1px solid ${active ? `color-mix(in srgb, ${stMeta.color} 33%, transparent)` : 'var(--border)'}`,
                    cursor:     'pointer',
                  }}
                >
                  {stMeta.label}
                </button>
              )
            })}
          </div>
          <button
            onClick={openGmail}
            className="btn btn-ghost text-xs mt-3 flex items-center gap-1.5"
          >
            <ExternalLink size={12} /> Open in Gmail
          </button>
        </div>
      </div>
    </>
  )
}
