import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { CheckCircle2, XCircle, Send as SendIcon, Settings, ExternalLink, RefreshCw, Search } from 'lucide-react'
import { useStore } from '../../store'
import { contactsApi } from '../../api/contacts'
import { sendApi } from '../../api/send'
import { inboxApi } from '../../api/inbox'
import { useAutomationConfig } from '../../hooks/useAutomationConfig'
import type { SendResult } from '../../api/send'
import { STATUS_META, SENT_STATUSES } from '../../types'
import ConfirmDialog from '../shared/ConfirmDialog'
import type { Contact, ContactStatus } from '../../types'
import { contactDisplayName, isGenericName } from '../../lib/display'
import { useAllDrafts } from '../../hooks/useAllDrafts'

// Outcomes a user records by hand as a conversation progresses.
const OUTCOME_STEPS: ContactStatus[] = ['replied', 'interview', 'offer', 'rejected']

// Send in small chunks (one request each) so a serverless backend never has to
// hold one giant request past its execution limit, and the UI can show progress.
const SEND_CHUNK_SIZE = 5

// Gmail's compose URL truncates very long bodies; beyond this we copy the body
// to the clipboard instead of losing the tail silently.
const MAX_GMAIL_URL = 1900

// Contact rows rendered before "Show more" takes over.
const PAGE_SIZE = 100

export default function Send() {
  const { contacts, drafts, upsertContact, setContacts, gmailAddress, gmailAppPassword, setActiveTab } = useStore()
  const qc = useQueryClient()
  const [showConfirm, setShowConfirm] = useState(false)
  const [results, setResults] = useState<SendResult[] | null>(null)
  const [sending, setSending] = useState(false)
  const [sendingId, setSendingId] = useState<number | null>(null)
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)
  const [checkingReplies, setCheckingReplies] = useState(false)
  const [search, setSearch] = useState('')
  // Render the list in pages: at 300+ contacts, mounting every card (each
  // with its pill row) is noticeably slow on mid phones.
  const [visibleLimit, setVisibleLimit] = useState(PAGE_SIZE)

  // Drafts come from a shared query so Compose and Send don't each refetch them.
  const { draftsLoaded } = useAllDrafts()

  // Creds saved server-side (encrypted) mean sending works with no local input.
  // Shared config query — same cache as Today and Setup.
  const { data: cfg } = useAutomationConfig()
  const serverGmail = { has: cfg?.has_gmail ?? false, address: cfg?.gmail_address ?? '' }

  const withDraft = contacts.filter(c => (drafts[c.id] ?? []).some(d => !d.is_followup))
  // Mirror the backend guard: a contact already actioned (emailed in any later
  // state) must not be re-sent a first-touch. Keeps the "Send All (N)" count honest.
  const ACTIONED = ['emailed', 'followed_up', 'replied', 'interview', 'offer', 'rejected', 'bounced']
  // Ungrounded role-inbox guesses (no real evidence the address exists) are
  // excluded from bulk "Send All" — they're the leads most likely to bounce.
  // Still shown in the list and individually sendable, just not auto-included.
  const isUnverifiedGuess = (c: Contact) => (c.designation || '').toLowerCase().includes('unverified guess')
  const sendable = withDraft.filter(c => !ACTIONED.includes(c.status) && !c.last_emailed_at)
  const unsent = sendable.filter(c => !isUnverifiedGuess(c))
  const sentCount = contacts.filter(c => SENT_STATUSES.includes(c.status)).length

  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: number; status: ContactStatus }) =>
      contactsApi.setStatus(id, status),
    // Optimistic: the pill reflects the tap instantly instead of freezing the
    // whole row for a server round-trip; rolled back if the request fails.
    onMutate: ({ id, status }) => {
      const prev = useStore.getState().contacts.find(c => c.id === id)
      if (prev) upsertContact({ ...prev, status })
      return { prev }
    },
    onSuccess: (updated) => {
      upsertContact(updated)
      qc.invalidateQueries({ queryKey: ['contacts'] })
    },
    onError: (e: Error, _vars, ctx) => {
      if (ctx?.prev) upsertContact(ctx.prev)
      toast.error(e.message)
    },
  })

  const refreshContacts = async () => {
    try {
      setContacts(await contactsApi.list())
      qc.invalidateQueries({ queryKey: ['contacts'] })
    } catch { /* next tab visit will refetch */ }
  }

  const openGmail = async (
    email: string, subject: string, body: string, contactId: number,
    newStatus: ContactStatus, prevStatus: ContactStatus,
  ) => {
    const base = `https://mail.google.com/mail/?view=cm&to=${encodeURIComponent(email)}&su=${encodeURIComponent(subject)}`
    const url = `${base}&body=${encodeURIComponent(body)}`
    if (url.length > MAX_GMAIL_URL) {
      // Gmail silently cuts long bodies off — copy instead so nothing is lost.
      // Open the window BEFORE the clipboard await: awaiting first drops the
      // click's user activation, so Safari/Firefox block the popup entirely.
      const win = window.open('', '_blank')
      try {
        await navigator.clipboard.writeText(body)
        toast('Email is long, so the body was copied — paste it into the Gmail window', { icon: '📋', duration: 6000 })
      } catch {
        toast('Email is long — Gmail may cut off the end. Review before sending.', { icon: '⚠️', duration: 6000 })
      }
      if (win) {
        win.location.href = base
      } else {
        // Nothing opened, so nothing was sent — don't mark the contact.
        toast('Popup blocked — allow popups for this site, then try again', { icon: '🚫', duration: 6000 })
        return
      }
    } else {
      window.open(url, '_blank')
    }
    // Opening compose isn't proof it was sent — mark it, but offer an undo.
    statusMutation.mutate({ id: contactId, status: newStatus })
    toast(t => (
      <span className="flex items-center gap-3 text-sm">
        Marked as {STATUS_META[newStatus].label.toLowerCase()}
        <button
          onClick={() => { statusMutation.mutate({ id: contactId, status: prevStatus }); toast.dismiss(t.id) }}
          style={{ color: 'var(--accent)', fontWeight: 600, background: 'none', border: 'none', cursor: 'pointer' }}
        >
          Undo
        </button>
      </span>
    ), { duration: 5000 })
  }

  const handleSendAll = async () => {
    setShowConfirm(false)
    setSending(true)
    setResults(null)

    const ids = unsent.map(c => c.id)
    const chunks: number[][] = []
    for (let i = 0; i < ids.length; i += SEND_CHUNK_SIZE) chunks.push(ids.slice(i, i + SEND_CHUNK_SIZE))

    const all: SendResult[] = []
    let deferred = 0
    let aborted = false
    setProgress({ done: 0, total: ids.length })

    for (const chunk of chunks) {
      try {
        const res = await sendApi.bulk(chunk, gmailAddress, gmailAppPassword)
        all.push(...res.results)
        deferred += res.deferred
        setResults([...all])
        setProgress({ done: Math.min(all.length + deferred, ids.length), total: ids.length })
        if (res.deferred > 0) break   // daily cap reached — stop cleanly
      } catch (e: any) {
        toast.error(e.message)
        aborted = true
        break                          // bad credentials / server error — don't hammer on
      }
    }

    // One refresh for all status changes (the backend already marked them).
    await refreshContacts()

    const sent = all.filter(r => r.status === 'sent').length
    const failed = all.filter(r => r.status === 'failed').length
    if (!aborted) {
      const deferNote = deferred ? ` · ${deferred} held for tomorrow (daily limit)` : ''
      if (failed === 0 && sent > 0) toast.success(`Sent ${sent} email${sent !== 1 ? 's' : ''}${deferNote}`)
      else if (sent > 0 || failed > 0) toast(`${sent} sent · ${failed} failed${deferNote}`, { icon: '⚠️' })
    } else if (sent > 0) {
      toast(`${sent} email${sent !== 1 ? 's' : ''} were sent before the error`, { icon: 'ℹ️' })
    }

    setSending(false)
    setProgress(null)
  }

  // Send ONE contact's draft over SMTP — for people who want to pick and
  // choose instead of firing "Send All".
  const sendOne = async (contactId: number, name: string) => {
    if (noCredentials) { toast.error('Add your Gmail and App Password in Setup first'); return }
    setSendingId(contactId)
    try {
      const res = await sendApi.bulk([contactId], gmailAddress, gmailAppPassword)
      const r = res.results[0]
      if (r?.status === 'sent') toast.success(`Sent to ${name}`)
      else if (res.deferred > 0) toast('Held back by today\'s sending limit — try tomorrow', { icon: '⏳' })
      else toast.error(r?.error || `Couldn't send to ${name}`)
      await refreshContacts()
    } catch (e: any) {
      toast.error(e.message)
    } finally {
      setSendingId(null)
    }
  }

  const handleCheckReplies = async () => {
    if (noCredentials) { toast.error('Add your Gmail and App Password in Setup first'); return }
    setCheckingReplies(true)
    try {
      const res = await inboxApi.sync(gmailAddress, gmailAppPassword)
      await refreshContacts()
      const bounceNote = res.bounces_found ? ` · ${res.bounces_found} bounced` : ''
      if (res.replies_found === 0 && res.bounces_found === 0) {
        toast(`No new replies yet (checked ${res.scanned} contact${res.scanned !== 1 ? 's' : ''})`, { icon: '📭' })
      } else if (res.replies_found === 0) {
        toast(`${res.bounces_found} email${res.bounces_found !== 1 ? 's' : ''} bounced`, { icon: '⚠️' })
      } else {
        toast.success(`${res.replies_found} new ${res.replies_found === 1 ? 'reply' : 'replies'}${bounceNote}`)
      }
    } catch (e: any) {
      toast.error(e.message)
    } finally {
      setCheckingReplies(false)
    }
  }

  const noCredentials = !serverGmail.has && (!gmailAddress || !gmailAppPassword)
  const sendingFrom = gmailAddress || serverGmail.address || 'your connected Gmail'

  // Text filter over the visible rows — at 100+ contacts, finding one person
  // to send/re-check shouldn't mean scrolling the whole list.
  const q = search.trim().toLowerCase()
  const visibleContacts = !q ? contacts : contacts.filter(c =>
    [c.name, c.company, c.email, c.designation].some(v => (v || '').toLowerCase().includes(q))
  )

  if (contacts.length === 0) return (
    <div className="text-center py-20">
      <p className="text-sm font-mono" style={{ color: 'var(--text-muted)' }}>No contacts yet</p>
    </div>
  )

  if (!draftsLoaded) return (
    <div className="space-y-4 animate-pulse" aria-hidden>
      <div className="flex items-start justify-between">
        <div>
          <div className="h-8 rounded w-28 mb-2" style={{ background: 'var(--surface-3)' }} />
          <div className="h-4 rounded w-48" style={{ background: 'var(--surface-2)' }} />
        </div>
        <div className="flex gap-2">
          <div className="h-9 rounded-lg w-32" style={{ background: 'var(--surface-3)' }} />
          <div className="h-9 rounded-lg w-28" style={{ background: 'var(--surface-3)' }} />
        </div>
      </div>
      <div className="grid grid-cols-3 gap-3">
        {[0, 1, 2].map(i => (
          <div key={i} className="card text-center" style={{ padding: 16 }}>
            <div className="h-8 rounded w-12 mx-auto mb-2" style={{ background: 'var(--surface-3)' }} />
            <div className="h-3 rounded w-16 mx-auto" style={{ background: 'var(--surface-2)' }} />
          </div>
        ))}
      </div>
      {[0, 1, 2, 3].map(i => (
        <div key={i} className="card" style={{ padding: 16 }}>
          <div className="flex items-center justify-between">
            <div className="space-y-2 flex-1">
              <div className="h-4 rounded w-1/3" style={{ background: 'var(--surface-3)' }} />
              <div className="h-3 rounded w-1/2" style={{ background: 'var(--surface-2)' }} />
            </div>
            <div className="h-8 rounded-lg w-20" style={{ background: 'var(--surface-2)' }} />
          </div>
        </div>
      ))}
    </div>
  )

  return (
    <div className="space-y-5">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      {/* flex-wrap: on narrow phones the action buttons drop below the title
          instead of pushing the page into horizontal scroll */}
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold tracking-wide mb-1" style={{ fontFamily: 'var(--font-display)' }}>Send Mail</h1>
          <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
            {withDraft.length} emails ready · {sentCount} sent/in progress
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={handleCheckReplies}
            disabled={checkingReplies || noCredentials}
            title={noCredentials
              ? 'Connect Gmail in Setup to check replies'
              : 'Scan your Gmail inbox for replies and update statuses'}
            className="btn flex items-center gap-2 text-sm font-medium"
            style={{
              background: 'color-mix(in srgb, var(--info) 10%, transparent)',
              borderColor: 'color-mix(in srgb, var(--info) 30%, transparent)',
              color: 'var(--info-text)',
              padding: '8px 14px',
              opacity: noCredentials ? 0.5 : 1,
            }}
          >
            <RefreshCw size={14} className={checkingReplies ? 'animate-spin' : ''} />
            {checkingReplies ? 'Checking…' : 'Check Replies'}
          </button>

          {unsent.length > 0 && (
            <button
              onClick={() => {
                if (noCredentials) toast.error('Add your Gmail and App Password in Setup first')
                else setShowConfirm(true)
              }}
              disabled={sending}
              className="btn flex items-center gap-2 text-sm font-semibold"
              style={{
                background: noCredentials ? 'var(--status-new-tint)' : 'color-mix(in srgb, var(--accent) 12%, transparent)',
                borderColor: noCredentials ? 'var(--border)' : 'color-mix(in srgb, var(--accent) 35%, transparent)',
                color: noCredentials ? 'var(--text-dim)' : 'var(--accent-text)',
                padding: '8px 16px',
              }}
            >
              <SendIcon size={14} />
              {sending && progress
                ? `Sending ${progress.done}/${progress.total}…`
                : sending ? 'Sending…' : `Send All (${unsent.length})`}
            </button>
          )}
        </div>
      </div>

      {/* ── No credentials banner ───────────────────────────────────────────── */}
      {noCredentials && withDraft.length > 0 && (
        <div
          className="rounded-xl p-4 flex items-center justify-between gap-4"
          style={{
            background: 'color-mix(in srgb, var(--warning) 8%, transparent)',
            border: '1px solid color-mix(in srgb, var(--warning) 20%, transparent)',
          }}
        >
          <div>
            <p className="text-sm font-medium" style={{ color: 'var(--warning-text)' }}>Gmail not connected</p>
            <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
              Connect your Gmail in Setup once — credentials are verified, stored
              encrypted, and sending works from then on without re-entering them.
            </p>
          </div>
          <button
            onClick={() => setActiveTab('setup')}
            className="btn text-xs flex items-center gap-1 flex-shrink-0"
            style={{ color: 'var(--accent-text)', borderColor: 'color-mix(in srgb, var(--accent) 25%, transparent)' }}
          >
            <Settings size={12} /> Setup
          </button>
        </div>
      )}

      {/* ── Confirm modal ───────────────────────────────────────────────────── */}
      {showConfirm && (
        <ConfirmDialog
          title={`Send ${unsent.length} email${unsent.length !== 1 ? 's' : ''}?`}
          confirmLabel="Send all now"
          onConfirm={handleSendAll}
          onCancel={() => setShowConfirm(false)}
        >
          <p>
            Sending from <strong style={{ color: 'var(--text)' }}>{sendingFrom}</strong> via Gmail.
            Each contact will be marked as <em>Emailed</em>.
          </p>
          <div className="space-y-1 max-h-40 overflow-y-auto">
            {unsent.map(c => (
              <div key={c.id} className="flex items-center gap-2 text-xs font-mono" style={{ color: 'var(--text-muted)' }}>
                <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: 'var(--accent)' }} />
                {c.name} · {c.email}
              </div>
            ))}
          </div>
        </ConfirmDialog>
      )}

      {/* ── Send results ────────────────────────────────────────────────────── */}
      {results && (
        <div className="card space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-bold font-mono tracking-widest" style={{ color: 'var(--text-muted)' }}>
              SEND RESULTS
            </span>
            <button onClick={() => setResults(null)} className="text-xs" style={{ color: 'var(--text-muted)' }}>
              dismiss
            </button>
          </div>
          {results.map(r => (
            <div key={r.contact_id} className="flex items-center gap-3 text-sm">
              {r.status === 'sent'
                ? <CheckCircle2 size={14} style={{ color: 'var(--success)', flexShrink: 0 }} />
                : <XCircle      size={14} style={{ color: 'var(--danger)', flexShrink: 0 }} />
              }
              <span className="flex-1 truncate">{r.name} · {r.email}</span>
              {r.status === 'failed' && (
                <span className="text-xs truncate max-w-[180px]" style={{ color: 'var(--danger-text)' }} title={r.error}>
                  {r.error}
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      {/* ── Stats ───────────────────────────────────────────────────────────── */}
      <div className="grid grid-cols-3 gap-3">
        {[
          { label: 'Total',    value: contacts.length,  color: 'var(--text-muted)' },
          { label: 'Ready',    value: withDraft.length, color: 'var(--status-replied)' },
          { label: 'Actioned', value: sentCount,        color: 'var(--success)' },
        ].map(stat => (
          <div key={stat.label} className="card text-center">
            <div className="text-2xl font-bold tnum" style={{ color: stat.color }}>{stat.value}</div>
            <div className="text-xs font-mono mt-1" style={{ color: 'var(--text-muted)' }}>{stat.label}</div>
          </div>
        ))}
      </div>

      {/* ── Search (only useful once the list is long) ──────────────────────── */}
      {contacts.length > 5 && (
        <div className="relative">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: 'var(--text-dim)' }} />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Filter by name, company, or email…"
            className="input text-sm w-full"
            style={{ paddingLeft: 32 }}
            aria-label="Filter contacts"
          />
        </div>
      )}

      {/* ── Contact rows ────────────────────────────────────────────────────── */}
      <div className="space-y-2">
        {visibleContacts.length === 0 && search.trim() && (
          <p className="text-sm text-center py-8" style={{ color: 'var(--text-muted)' }}>
            No contacts match “{search.trim()}”
          </p>
        )}
        {visibleContacts.slice(0, visibleLimit).map(c => {
          const first    = (drafts[c.id] ?? []).find(d => !d.is_followup)
          const followup = (drafts[c.id] ?? []).find(d => d.is_followup)
          // Already contacted + a follow-up draft exists → the Gmail button
          // sends the follow-up.
          const isFollowupSend = SENT_STATUSES.includes(c.status) && !!followup
          const draft = isFollowupSend ? followup : first
          const st = STATUS_META[c.status] ?? STATUS_META.new

          return (
            <div key={c.id} className="card">
              <div className="flex items-center justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium">{contactDisplayName(c)}</span>
                    {isGenericName(c.name) && (
                      <span className="text-xs font-mono" style={{ color: 'var(--text-muted)' }}>{c.email}</span>
                    )}
                    <span className="badge" style={{ background: st.bg, color: st.color }}>
                      {st.label}
                    </span>
                  </div>
                  <div className="text-xs font-mono truncate mt-0.5" style={{ color: 'var(--text-muted)' }}>
                    {isGenericName(c.name) ? c.company : `${c.email} · ${c.company}`}
                  </div>
                </div>

                <div className="flex items-center gap-2 flex-shrink-0">
                  {draft ? (
                    <>
                      {c.status === 'emailed' && (
                        <span className="flex items-center gap-1 text-xs" style={{ color: 'var(--success-text)' }}>
                          <CheckCircle2 size={12} /> Sent
                        </span>
                      )}
                      {/* Direct SMTP send for THIS contact only — first-touch
                          contacts, when Gmail is connected. */}
                      {!isFollowupSend && sendable.some(u => u.id === c.id) && !noCredentials && (
                        <button
                          onClick={() => sendOne(c.id, c.name)}
                          disabled={sendingId !== null || sending}
                          title={`Send this email to ${c.name} now`}
                          className="btn text-xs flex items-center gap-1 font-semibold"
                          style={{
                            background: 'color-mix(in srgb, var(--success) 12%, transparent)',
                            borderColor: 'color-mix(in srgb, var(--success) 35%, transparent)',
                            color: 'var(--success-text)',
                            opacity: sendingId !== null && sendingId !== c.id ? 0.5 : 1,
                          }}
                        >
                          {sendingId === c.id
                            ? <><RefreshCw size={11} className="animate-spin" /> Sending…</>
                            : <><SendIcon size={11} /> Send</>}
                        </button>
                      )}
                      <button
                        onClick={() => openGmail(
                          c.email, draft.subject, draft.body, c.id,
                          isFollowupSend ? 'followed_up' : 'emailed',
                          c.status,
                        )}
                        title={isFollowupSend
                          ? 'Open Gmail with the follow-up draft'
                          : 'Open Gmail with this draft'}
                        className="btn text-xs flex items-center gap-1"
                        style={isFollowupSend ? {
                          background: 'color-mix(in srgb, var(--info) 10%, transparent)',
                          borderColor: 'color-mix(in srgb, var(--info) 30%, transparent)',
                          color: 'var(--info-text)',
                        } : {
                          background: 'var(--accent-dim)',
                          borderColor: 'color-mix(in srgb, var(--accent) 25%, transparent)',
                          color: 'var(--accent-text)',
                        }}
                      >
                        <ExternalLink size={11} />
                        {isFollowupSend ? 'Send follow-up' : 'Gmail'}
                      </button>
                    </>
                  ) : (
                    <span className="text-xs font-mono" style={{ color: 'var(--text-dim)' }}>
                      No draft
                    </span>
                  )}
                </div>
              </div>

              {/* Outcome capture — once a contact has been emailed, record what
                  happened so the Today funnel reflects real results. */}
              {SENT_STATUSES.includes(c.status) && (
                <div className="flex items-center gap-1.5 mt-2.5 pt-2.5 flex-wrap" style={{ borderTop: '1px solid var(--border)' }}>
                  <span className="text-[11px] font-mono font-bold tracking-widest mr-1" style={{ color: 'var(--text-muted)' }}>
                    OUTCOME
                  </span>
                  {OUTCOME_STEPS.map(s => {
                    const meta = STATUS_META[s]
                    const active = c.status === s
                    return (
                      <button
                        key={s}
                        onClick={() => statusMutation.mutate({ id: c.id, status: s })}
                        className="text-[11px] px-2 py-0.5 rounded-full font-semibold transition-all hit-target"
                        style={{
                          background: active ? meta.bg : 'transparent',
                          // meta.color is a var() — alpha via color-mix, never string-suffix tricks
                          color:      active ? meta.color : 'var(--text-muted)',
                          border:     `1px solid ${active ? `color-mix(in srgb, ${meta.color} 33%, transparent)` : 'var(--border)'}`,
                        }}
                      >
                        {meta.label}
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
        {visibleContacts.length > visibleLimit && (
          <button
            onClick={() => setVisibleLimit(l => l + PAGE_SIZE)}
            className="btn btn-ghost w-full justify-center text-sm"
          >
            Show {Math.min(PAGE_SIZE, visibleContacts.length - visibleLimit)} more
            <span className="tnum" style={{ color: 'var(--text-muted)' }}>
              &nbsp;({visibleLimit} of {visibleContacts.length})
            </span>
          </button>
        )}
      </div>
    </div>
  )
}
