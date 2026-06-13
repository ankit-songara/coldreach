import { useState, useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { CheckCircle2, XCircle, Send as SendIcon, Settings, ExternalLink, RefreshCw, Clock } from 'lucide-react'
import { useStore } from '../../store'
import { contactsApi } from '../../api/contacts'
import { composeApi } from '../../api/compose'
import { sendApi } from '../../api/send'
import { inboxApi } from '../../api/inbox'
import AutomationPanel from './AutomationPanel'
import type { SendResult } from '../../api/send'
import { STATUS_META } from '../../types'
import type { ContactStatus } from '../../types'

export default function Send() {
  const { contacts, drafts, upsertContact, setDrafts, gmailAddress, gmailAppPassword, setActiveTab } = useStore()
  const qc = useQueryClient()
  const [showConfirm, setShowConfirm] = useState(false)
  const [results, setResults] = useState<SendResult[] | null>(null)
  const [sending, setSending] = useState(false)
  const [checkingReplies, setCheckingReplies] = useState(false)
  const [showSchedule, setShowSchedule] = useState(false)
  const [scheduleAt, setScheduleAt] = useState('')

  // Sync drafts from backend on mount so Send tab works even after refresh
  useEffect(() => {
    const contactsWithoutDrafts = contacts.filter(c => !(drafts[c.id]?.length))
    if (contactsWithoutDrafts.length === 0) return
    contactsWithoutDrafts.forEach(c => {
      composeApi.getDrafts(c.id).then(d => {
        if (d.length > 0) setDrafts(c.id, d)
      }).catch(() => {})
    })
  }, [contacts.length])

  const withDraft = contacts.filter(c => (drafts[c.id] ?? []).some(d => !d.is_followup))
  const unsent = withDraft.filter(c => c.status !== 'emailed')
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
  })

  const openGmail = (email: string, subject: string, body: string, contactId: number) => {
    const url = `https://mail.google.com/mail/?view=cm&to=${encodeURIComponent(email)}&su=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`
    window.open(url, '_blank')
    statusMutation.mutate({ id: contactId, status: 'emailed' })
    toast.success(`Opened Gmail for ${email}`)
  }

  const handleSendAll = async () => {
    setShowConfirm(false)
    setSending(true)
    setResults(null)
    console.log('[SendAll] starting, gmail:', gmailAddress, 'contacts:', unsent.length)
    try {
      const res = await sendApi.bulk([], gmailAddress, gmailAppPassword)
      setResults(res.results)
      // Refresh contact statuses in store
      res.results
        .filter(r => r.status === 'sent')
        .forEach(r => statusMutation.mutate({ id: r.contact_id, status: 'emailed' }))
      const deferNote = res.deferred ? ` · ${res.deferred} deferred (daily cap)` : ''
      if (res.failed === 0) {
        toast.success(`Sent ${res.sent} emails${deferNote}`)
      } else {
        toast(`${res.sent} sent · ${res.failed} failed${deferNote}`, { icon: '⚠️' })
      }
    } catch (e: any) {
      toast.error(e.response?.data?.detail ?? e.message)
    } finally {
      setSending(false)
    }
  }

  const handleCheckReplies = async () => {
    if (noCredentials) { toast.error('Add Gmail credentials in Setup first'); return }
    setCheckingReplies(true)
    try {
      const res = await inboxApi.sync(gmailAddress, gmailAppPassword)
      // Refresh contacts so newly-replied ones update in the UI
      const fresh = await contactsApi.list()
      fresh.forEach(c => upsertContact(c))
      qc.invalidateQueries({ queryKey: ['contacts'] })
      const bounceNote = res.bounces_found ? ` · ${res.bounces_found} bounced` : ''
      if (res.replies_found === 0 && res.bounces_found === 0) {
        toast(`No new replies (scanned ${res.scanned})`, { icon: '📭' })
      } else if (res.replies_found === 0) {
        toast(`${res.bounces_found} bounced`, { icon: '⚠️' })
      } else {
        toast.success(
          `${res.replies_found} new ${res.replies_found === 1 ? 'reply' : 'replies'}` +
          bounceNote +
          (res.followups_cancelled ? ` · ${res.followups_cancelled} follow-ups cancelled` : '')
        )
      }
    } catch (e: any) {
      toast.error(e.response?.data?.detail ?? e.message)
    } finally {
      setCheckingReplies(false)
    }
  }

  const handleSchedule = async () => {
    if (!scheduleAt) { toast.error('Pick a date & time'); return }
    const iso = new Date(scheduleAt).toISOString()
    if (new Date(scheduleAt).getTime() <= Date.now()) { toast.error('Pick a future time'); return }
    try {
      const res = await sendApi.schedule([], iso)
      if (res.scheduled === 0) {
        toast(`Nothing scheduled (${res.skipped} skipped)`, { icon: '📭' })
      } else {
        toast.success(`Scheduled ${res.scheduled} emails for ${new Date(scheduleAt).toLocaleString()}`)
      }
      setShowSchedule(false)
      setScheduleAt('')
    } catch (e: any) {
      toast.error(e.response?.data?.detail ?? e.message)
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
          <h1 className="text-2xl font-bold tracking-wide mb-1" style={{ fontFamily: 'Rajdhani' }}>Send</h1>
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
              background: 'rgba(167,139,250,0.10)',
              borderColor: 'rgba(167,139,250,0.30)',
              color: '#a78bfa',
              padding: '8px 14px',
              opacity: noCredentials ? 0.5 : 1,
            }}
          >
            <RefreshCw size={14} className={checkingReplies ? 'animate-spin' : ''} />
            {checkingReplies ? 'Checking…' : 'Check Replies'}
          </button>

          {unsent.length > 0 && (
            <>
              <button
                onClick={() => setShowSchedule(s => !s)}
                disabled={sending}
                title="Schedule these emails for a future time"
                className="btn flex items-center gap-2 text-sm font-medium"
                style={{
                  background: 'rgba(245,158,11,0.10)',
                  borderColor: 'rgba(245,158,11,0.30)',
                  color: '#f59e0b',
                  padding: '8px 14px',
                }}
              >
                <Clock size={14} /> Schedule
              </button>
              <button
                onClick={() => {
                  noCredentials ? toast.error('Add Gmail credentials in Setup first') : setShowConfirm(true)
                }}
                disabled={sending}
                className="btn flex items-center gap-2 text-sm font-semibold"
                style={{
                  background: noCredentials ? 'rgba(100,116,139,0.10)' : 'rgba(34,211,238,0.12)',
                  borderColor: noCredentials ? 'var(--border)' : 'rgba(34,211,238,0.35)',
                  color: noCredentials ? 'var(--text-dim)' : 'var(--accent)',
                  padding: '8px 16px',
                }}
              >
                <SendIcon size={14} />
                {sending ? 'Sending…' : `Send All (${unsent.length})`}
              </button>
            </>
          )}
        </div>
      </div>

      {/* ── Schedule send popover ───────────────────────────────────────────── */}
      {showSchedule && (
        <div className="card flex items-end gap-3 flex-wrap" style={{ border: '1px solid rgba(245,158,11,0.25)' }}>
          <div className="flex-1 min-w-[220px]">
            <div className="text-xs font-bold font-mono mb-1.5" style={{ color: '#f59e0b' }}>
              SCHEDULE {unsent.length} EMAILS
            </div>
            <input
              type="datetime-local"
              value={scheduleAt}
              onChange={e => setScheduleAt(e.target.value)}
              className="input text-sm w-full"
            />
            <p className="text-xs mt-1.5" style={{ color: 'var(--text-dim)' }}>
              Requires automation enabled below (stores Gmail creds so the scheduler can send).
            </p>
          </div>
          <button onClick={handleSchedule} className="btn text-sm font-semibold"
            style={{ background: 'rgba(245,158,11,0.12)', borderColor: 'rgba(245,158,11,0.35)', color: '#f59e0b' }}>
            Schedule
          </button>
          <button onClick={() => setShowSchedule(false)} className="btn btn-ghost text-sm" style={{ color: 'var(--text-muted)' }}>
            Cancel
          </button>
        </div>
      )}

      {/* ── Follow-up automation ────────────────────────────────────────────── */}
      <AutomationPanel />

      {/* ── No credentials banner ───────────────────────────────────────────── */}
      {noCredentials && withDraft.length > 0 && (
        <div
          className="rounded-xl p-4 flex items-center justify-between gap-4"
          style={{ background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.2)' }}
        >
          <div>
            <p className="text-sm font-medium" style={{ color: '#f59e0b' }}>Gmail not connected</p>
            <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
              Add your Gmail + App Password in Setup to send emails directly.
            </p>
          </div>
          <button
            onClick={() => setActiveTab('setup')}
            className="btn text-xs flex items-center gap-1 flex-shrink-0"
            style={{ color: 'var(--accent)', borderColor: 'rgba(34,211,238,0.25)' }}
          >
            <Settings size={12} /> Setup
          </button>
        </div>
      )}

      {/* ── Confirm modal ───────────────────────────────────────────────────── */}
      {showConfirm && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          style={{ background: 'rgba(0,0,0,0.6)' }}
          onClick={() => setShowConfirm(false)}
        >
          <div
            className="card w-full max-w-sm mx-4 space-y-4"
            style={{ border: '1px solid rgba(34,211,238,0.25)' }}
            onClick={e => e.stopPropagation()}
          >
            <h3 className="font-bold text-base">Send {unsent.length} emails?</h3>
            <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
              Sending from <strong style={{ color: 'var(--text)' }}>{gmailAddress}</strong> via Gmail SMTP.
              All contacts will be marked as <em>Emailed</em>.
            </p>
            <div className="space-y-1 max-h-40 overflow-y-auto">
              {unsent.map(c => (
                <div key={c.id} className="flex items-center gap-2 text-xs font-mono" style={{ color: 'var(--text-dim)' }}>
                  <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: 'var(--accent)' }} />
                  {c.name} · {c.email}
                </div>
              ))}
            </div>
            <div className="flex gap-2 pt-1">
              <button
                onClick={handleSendAll}
                className="btn flex-1 flex items-center justify-center gap-2 font-semibold"
                style={{
                  background: 'rgba(34,211,238,0.15)',
                  borderColor: 'rgba(34,211,238,0.4)',
                  color: 'var(--accent)',
                }}
              >
                <SendIcon size={13} /> Send all now
              </button>
              <button
                onClick={() => setShowConfirm(false)}
                className="btn"
                style={{ color: 'var(--text-muted)', borderColor: 'var(--border)' }}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
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
                ? <CheckCircle2 size={14} style={{ color: '#34d399', flexShrink: 0 }} />
                : <XCircle      size={14} style={{ color: '#ef4444', flexShrink: 0 }} />
              }
              <span className="flex-1 truncate">{r.name} · {r.email}</span>
              {r.status === 'failed' && (
                <span className="text-xs truncate max-w-[180px]" style={{ color: '#ef4444' }}>{r.error}</span>
              )}
            </div>
          ))}
        </div>
      )}

      {/* ── Stats ───────────────────────────────────────────────────────────── */}
      <div className="grid grid-cols-3 gap-3">
        {[
          { label: 'Total',    value: contacts.length,  color: 'var(--text-muted)' },
          { label: 'Ready',    value: withDraft.length, color: '#22d3ee' },
          { label: 'Actioned', value: sentCount,        color: '#34d399' },
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
          const draft = (drafts[c.id] ?? []).find(d => !d.is_followup)
          const st = STATUS_META[c.status] ?? STATUS_META.new

          return (
            <div key={c.id} className="card flex items-center justify-between gap-4">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{c.name}</span>
                  <span className="badge" style={{ background: st.bg, color: st.color, fontSize: '9px' }}>
                    {st.label}
                  </span>
                </div>
                <div className="text-xs font-mono truncate mt-0.5" style={{ color: 'var(--text-dim)' }}>
                  {c.email} · {c.company}
                </div>
              </div>

              <div className="flex items-center gap-2 flex-shrink-0">
                {draft ? (
                  <>
                    {c.status === 'emailed' && (
                      <span className="flex items-center gap-1 text-xs" style={{ color: '#34d399' }}>
                        <CheckCircle2 size={12} /> Sent
                      </span>
                    )}
                    <button
                      onClick={() => openGmail(c.email, draft.subject, draft.body, c.id)}
                      className="btn text-xs flex items-center gap-1"
                      style={{
                        background: 'rgba(34,211,238,0.10)',
                        borderColor: 'rgba(34,211,238,0.25)',
                        color: 'var(--accent)',
                      }}
                    >
                      <ExternalLink size={11} />
                      Gmail
                    </button>
                  </>
                ) : (
                  <span className="text-xs font-mono" style={{ color: 'var(--text-dim)' }}>
                    No draft
                  </span>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
