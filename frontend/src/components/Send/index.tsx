import { useState, useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { CheckCircle2, XCircle, Send as SendIcon, Settings, ExternalLink, RefreshCw } from 'lucide-react'
import { useStore } from '../../store'
import { contactsApi } from '../../api/contacts'
import { composeApi } from '../../api/compose'
import { sendApi } from '../../api/send'
import { inboxApi } from '../../api/inbox'
import type { SendResult } from '../../api/send'
import { STATUS_META } from '../../types'
import EmailBadge from '../shared/EmailBadge'
import ConfirmDialog from '../shared/ConfirmDialog'
import type { ContactStatus, Draft } from '../../types'

// A contact that has received a first-touch email — eligible for outcome capture.
const CONTACTED = ['emailed', 'followed_up', 'replied', 'interview', 'offer', 'rejected']
// Outcomes a user records by hand as a conversation progresses.
const OUTCOME_STEPS: ContactStatus[] = ['replied', 'interview', 'offer', 'rejected']

// Send in small chunks (one request each) so a serverless backend never has to
// hold one giant request past its execution limit, and the UI can show progress.
const SEND_CHUNK_SIZE = 5

// Gmail's compose URL truncates very long bodies; beyond this we copy the body
// to the clipboard instead of losing the tail silently.
const MAX_GMAIL_URL = 1900

export default function Send() {
  const { contacts, drafts, upsertContact, setContacts, setDrafts, gmailAddress, gmailAppPassword, setActiveTab } = useStore()
  const qc = useQueryClient()
  const [showConfirm, setShowConfirm] = useState(false)
  const [results, setResults] = useState<SendResult[] | null>(null)
  const [sending, setSending] = useState(false)
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)
  const [checkingReplies, setCheckingReplies] = useState(false)

  // Sync drafts from backend on mount so Send tab works even after refresh —
  // ONE request for all contacts.
  useEffect(() => {
    if (contacts.length === 0) return
    if (contacts.every(c => drafts[c.id]?.length)) return
    composeApi.getAllDrafts().then(all => {
      const grouped: Record<number, Draft[]> = {}
      for (const d of all) (grouped[d.contact_id] ??= []).push(d)
      Object.entries(grouped).forEach(([cid, ds]) => setDrafts(Number(cid), ds))
    }).catch(() => {})
  }, [contacts.length]) // eslint-disable-line react-hooks/exhaustive-deps

  const withDraft = contacts.filter(c => (drafts[c.id] ?? []).some(d => !d.is_followup))
  // Mirror the backend guard: a contact already actioned (emailed in any later
  // state) must not be re-sent a first-touch. Keeps the "Send All (N)" count honest.
  const ACTIONED = ['emailed', 'followed_up', 'replied', 'interview', 'offer', 'rejected', 'bounced']
  const unsent = withDraft.filter(c => !ACTIONED.includes(c.status) && !c.last_emailed_at)
  const sentCount = contacts.filter(c =>
    ['emailed', 'followed_up', 'replied', 'interview'].includes(c.status)
  ).length

  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: number; status: ContactStatus }) =>
      contactsApi.setStatus(id, status),
    onSuccess: (updated) => {
      upsertContact(updated)
      qc.invalidateQueries({ queryKey: ['contacts'] })
    },
    onError: (e: Error) => toast.error(e.message),
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
    let url = `${base}&body=${encodeURIComponent(body)}`
    if (url.length > MAX_GMAIL_URL) {
      // Gmail silently cuts long bodies off — copy instead so nothing is lost.
      try {
        await navigator.clipboard.writeText(body)
        toast('Email is long, so the body was copied — paste it into the Gmail window', { icon: '📋', duration: 6000 })
      } catch {
        toast('Email is long — Gmail may cut off the end. Review before sending.', { icon: '⚠️', duration: 6000 })
      }
      url = base
    }
    window.open(url, '_blank')
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

  const noCredentials = !gmailAddress || !gmailAppPassword

  if (contacts.length === 0) return (
    <div className="text-center py-20">
      <p className="text-sm font-mono" style={{ color: 'var(--text-dim)' }}>No contacts yet</p>
    </div>
  )

  return (
    <div className="space-y-5">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-wide mb-1" style={{ fontFamily: 'var(--font-display)' }}>Send</h1>
          <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
            {withDraft.length} emails ready · {sentCount} sent/in progress
          </p>
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          <button
            onClick={handleCheckReplies}
            disabled={checkingReplies || noCredentials}
            title="Scan your Gmail inbox for replies and update statuses"
            className="btn flex items-center gap-2 text-sm font-medium"
            style={{
              background: 'rgba(111,90,224,0.10)',
              borderColor: 'rgba(111,90,224,0.30)',
              color: '#6f5ae0',
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
                background: noCredentials ? 'rgba(138,127,112,0.10)' : 'rgba(226,96,63,0.12)',
                borderColor: noCredentials ? 'var(--border)' : 'rgba(226,96,63,0.35)',
                color: noCredentials ? 'var(--text-dim)' : 'var(--accent)',
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
          style={{ background: 'rgba(196,125,30,0.08)', border: '1px solid rgba(196,125,30,0.2)' }}
        >
          <div>
            <p className="text-sm font-medium" style={{ color: '#c47d1e' }}>Gmail not connected</p>
            <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
              Add your Gmail address and App Password in Setup to send. For your security we
              never store the App Password in this browser, so you'll re-enter it after a refresh.
            </p>
          </div>
          <button
            onClick={() => setActiveTab('setup')}
            className="btn text-xs flex items-center gap-1 flex-shrink-0"
            style={{ color: 'var(--accent)', borderColor: 'rgba(226,96,63,0.25)' }}
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
            Sending from <strong style={{ color: 'var(--text)' }}>{gmailAddress}</strong> via Gmail.
            Each contact will be marked as <em>Emailed</em>.
          </p>
          <div className="space-y-1 max-h-40 overflow-y-auto">
            {unsent.map(c => (
              <div key={c.id} className="flex items-center gap-2 text-xs font-mono" style={{ color: 'var(--text-dim)' }}>
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
            <span className="text-xs font-bold font-mono tracking-widest" style={{ color: 'var(--text-dim)' }}>
              SEND RESULTS
            </span>
            <button onClick={() => setResults(null)} className="text-xs" style={{ color: 'var(--text-dim)' }}>
              dismiss
            </button>
          </div>
          {results.map(r => (
            <div key={r.contact_id} className="flex items-center gap-3 text-sm">
              {r.status === 'sent'
                ? <CheckCircle2 size={14} style={{ color: '#3f8f43', flexShrink: 0 }} />
                : <XCircle      size={14} style={{ color: '#d2483a', flexShrink: 0 }} />
              }
              <span className="flex-1 truncate">{r.name} · {r.email}</span>
              {r.status === 'failed' && (
                <span className="text-xs truncate max-w-[180px]" style={{ color: '#d2483a' }} title={r.error}>
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
          { label: 'Ready',    value: withDraft.length, color: '#0e9d88' },
          { label: 'Actioned', value: sentCount,        color: '#3f8f43' },
        ].map(stat => (
          <div key={stat.label} className="card text-center">
            <div className="text-2xl font-bold" style={{ color: stat.color }}>{stat.value}</div>
            <div className="text-xs font-mono mt-1" style={{ color: 'var(--text-dim)' }}>{stat.label}</div>
          </div>
        ))}
      </div>

      {/* ── Contact rows ────────────────────────────────────────────────────── */}
      <div className="space-y-2">
        {contacts.map(c => {
          const first    = (drafts[c.id] ?? []).find(d => !d.is_followup)
          const followup = (drafts[c.id] ?? []).find(d => d.is_followup)
          // Already contacted + a follow-up draft exists → the Gmail button
          // sends the follow-up (the manual replacement for the old scheduler).
          const isFollowupSend = CONTACTED.includes(c.status) && !!followup
          const draft = isFollowupSend ? followup : first
          const st = STATUS_META[c.status] ?? STATUS_META.new

          return (
            <div key={c.id} className="card">
              <div className="flex items-center justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium">{c.name}</span>
                    <span className="badge" style={{ background: st.bg, color: st.color, fontSize: '9px' }}>
                      {st.label}
                    </span>
                    <EmailBadge status={c.email_status} confidence={c.confidence} />
                  </div>
                  <div className="text-xs font-mono truncate mt-0.5" style={{ color: 'var(--text-dim)' }}>
                    {c.email} · {c.company}
                  </div>
                </div>

                <div className="flex items-center gap-2 flex-shrink-0">
                  {draft ? (
                    <>
                      {c.status === 'emailed' && (
                        <span className="flex items-center gap-1 text-xs" style={{ color: '#3f8f43' }}>
                          <CheckCircle2 size={12} /> Sent
                        </span>
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
                          background: 'rgba(111,90,224,0.10)',
                          borderColor: 'rgba(111,90,224,0.30)',
                          color: '#6f5ae0',
                        } : {
                          background: 'rgba(226,96,63,0.10)',
                          borderColor: 'rgba(226,96,63,0.25)',
                          color: 'var(--accent)',
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
              {CONTACTED.includes(c.status) && (
                <div className="flex items-center gap-1.5 mt-2.5 pt-2.5 flex-wrap" style={{ borderTop: '1px solid var(--border)' }}>
                  <span className="text-[10px] font-mono font-bold tracking-widest mr-1" style={{ color: 'var(--text-dim)' }}>
                    OUTCOME
                  </span>
                  {OUTCOME_STEPS.map(s => {
                    const meta = STATUS_META[s]
                    const active = c.status === s
                    return (
                      <button
                        key={s}
                        onClick={() => statusMutation.mutate({ id: c.id, status: s })}
                        disabled={statusMutation.isPending}
                        className="text-[10px] px-2 py-0.5 rounded-full font-semibold transition-all"
                        style={{
                          background: active ? meta.bg : 'transparent',
                          color:      active ? meta.color : 'var(--text-dim)',
                          border:     `1px solid ${active ? meta.color + '55' : 'var(--border)'}`,
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
      </div>
    </div>
  )
}
