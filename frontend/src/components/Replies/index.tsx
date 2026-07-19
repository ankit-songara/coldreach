import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { useStore } from '../../store'
import { contactsApi } from '../../api/contacts'
import { inboxApi } from '../../api/inbox'
import type { ReplyMessage } from '../../api/inbox'
import Logo from '../shared/Logo'
import { STATUS_META } from '../../types'
import type { ContactStatus } from '../../types'

// Outcomes recordable straight from a reply row. ('replied' is where the row
// already is — offering it as a button would be a no-op.)
const OUTCOMES: ContactStatus[] = ['interview', 'offer', 'rejected']

// NOTE: the /inbox/replies payload intentionally carries no email address, so
// there is no "Open in Gmail" button here — we never fabricate an address.
// Replying happens from the user's own Gmail thread.

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2)
    .map(w => w[0].toUpperCase()).join('') || '?'
}

// Backend timestamps are naive UTC (no zone suffix) — parse them as UTC, not
// local time, or "2h ago" would be off by the viewer's UTC offset.
function timeAgo(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')
  const s = (Date.now() - d.getTime()) / 1000
  if (!Number.isFinite(s)) return ''
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  if (s < 7 * 86400) return `${Math.floor(s / 86400)}d ago`
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

export default function Replies() {
  const { upsertContact } = useStore()
  const qc = useQueryClient()

  const { data: replies, isLoading, isError, refetch } = useQuery({
    queryKey: ['replies'],
    queryFn: inboxApi.replies,
  })

  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: number; status: ContactStatus }) =>
      contactsApi.setStatus(id, status),
    // Optimistic: the pill reflects the tap instantly instead of freezing the
    // whole row for a server round-trip; rolled back if the request fails.
    onMutate: ({ id, status }) => {
      const prev = useStore.getState().contacts.find(c => c.id === id)
      if (prev) upsertContact({ ...prev, status })
      // Reply rows carry their own status snapshot — flip those too, so the
      // active pill moves even before the refetch lands.
      const prevReplies = qc.getQueryData<ReplyMessage[]>(['replies'])
      qc.setQueryData<ReplyMessage[]>(['replies'], rows =>
        rows?.map(r => (r.contact_id === id ? { ...r, status } : r)))
      return { prev, prevReplies }
    },
    onSuccess: (updated) => {
      upsertContact(updated)
      qc.invalidateQueries({ queryKey: ['contacts'] })
      qc.invalidateQueries({ queryKey: ['replies'] })
    },
    onError: (e: Error, _vars, ctx) => {
      if (ctx?.prev) upsertContact(ctx.prev)
      if (ctx?.prevReplies) qc.setQueryData(['replies'], ctx.prevReplies)
      toast.error(e.message)
    },
  })

  return (
    <div className="space-y-5">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div>
        <h1 className="text-2xl font-bold tracking-wide mb-1" style={{ fontFamily: 'var(--font-display)' }}>Replies</h1>
        <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
          Read and triage without leaving — one tap records the outcome and moves the pipeline.
        </p>
      </div>

      {/* ── Loading skeleton ────────────────────────────────────────────────── */}
      {isLoading && (
        <div className="space-y-3 animate-pulse" aria-hidden>
          {[0, 1, 2].map(i => (
            <div key={i} className="card">
              <div className="flex items-center gap-3 mb-3">
                <div className="w-9 h-9 rounded-full flex-shrink-0" style={{ background: 'var(--surface-3)' }} />
                <div className="flex-1 space-y-2">
                  <div className="h-4 rounded w-1/3" style={{ background: 'var(--surface-3)' }} />
                  <div className="h-3 rounded w-1/2" style={{ background: 'var(--surface-2)' }} />
                </div>
              </div>
              <div className="h-14 rounded-xl" style={{ background: 'var(--surface-2)' }} />
            </div>
          ))}
        </div>
      )}

      {/* ── Error with retry ────────────────────────────────────────────────── */}
      {isError && !isLoading && (
        <div className="flex flex-col items-center py-16 px-6 text-center">
          <p className="text-sm mb-4" style={{ color: 'var(--text-muted)' }}>
            Couldn't load your replies — check your connection and try again.
          </p>
          <button
            onClick={() => refetch()}
            className="px-5 py-2 rounded-full text-sm font-bold"
            style={{ background: 'var(--accent)', color: 'var(--on-accent)', border: 'none', cursor: 'pointer' }}
          >
            Retry
          </button>
        </div>
      )}

      {/* ── Empty state ─────────────────────────────────────────────────────── */}
      {!isLoading && !isError && replies?.length === 0 && (
        <div className="flex flex-col items-center text-center py-20">
          {/* Animated brand mark: "empty inbox" is a sanctioned waiting
              surface in the logo kit — the typing dots ARE the message. */}
          <div style={{ marginBottom: 14 }}><Logo size={34} animated /></div>
          <p className="text-sm font-mono" style={{ color: 'var(--text-muted)' }}>
            No replies yet — they land here automatically after you send
          </p>
        </div>
      )}

      {/* ── Reply rows ──────────────────────────────────────────────────────── */}
      {!isLoading && !isError && (replies ?? []).map(r => {
        const st = STATUS_META[r.status] ?? STATUS_META.replied
        const when = timeAgo(r.received_at)
        return (
          <div key={r.id} className="card">
            <div className="flex items-center gap-3 mb-2.5">
              <span
                className="w-9 h-9 rounded-full flex items-center justify-center text-[11.5px] font-extrabold flex-shrink-0"
                style={{ background: st.bg, color: st.color }}
                aria-hidden
              >
                {initials(r.name)}
              </span>
              <div className="min-w-0">
                <div className="text-sm font-bold truncate">
                  {r.name}
                  {r.designation && (
                    <span className="font-medium text-[12.5px]" style={{ color: 'var(--text-muted)' }}> · {r.designation}</span>
                  )}
                </div>
                <div className="text-[10.5px] font-mono truncate" style={{ color: 'var(--text-dim)' }}>
                  {r.company}
                  {when && <> · {when}</>}
                  {' · '}{r.subject ? <>replied to "{r.subject}"</> : 'replied to your email'}
                </div>
              </div>
              <span className="badge ml-auto flex-shrink-0" style={{ background: st.bg, color: st.color }}>
                {st.label}
              </span>
            </div>

            {r.snippet && (
              <p
                className="text-[13.5px] mb-3 rounded-xl"
                style={{ color: 'var(--text-muted)', background: 'var(--surface-2)', padding: '11px 14px', lineHeight: 1.6, margin: '0 0 13px' }}
              >
                "{r.snippet}"
              </p>
            )}

            {/* Outcome capture — same pill idiom as Send Mail's OUTCOME row. */}
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="text-[11px] font-mono font-bold tracking-widest mr-1" style={{ color: 'var(--text-muted)' }}>
                OUTCOME
              </span>
              {OUTCOMES.map(s => {
                const meta = STATUS_META[s]
                const active = r.status === s
                return (
                  <button
                    key={s}
                    onClick={() => statusMutation.mutate({ id: r.contact_id, status: s })}
                    className="text-[11px] px-2 py-0.5 rounded-full font-semibold transition-all hit-target"
                    style={{
                      background: active ? meta.bg : 'transparent',
                      // meta.color is a var() — alpha via color-mix, never string-suffix tricks
                      color:      active ? meta.color : 'var(--text-muted)',
                      border:     `1px solid ${active ? `color-mix(in srgb, ${meta.color} 33%, transparent)` : 'var(--border)'}`,
                      cursor:     'pointer',
                    }}
                  >
                    {meta.label}
                  </button>
                )
              })}
            </div>
          </div>
        )
      })}
    </div>
  )
}
