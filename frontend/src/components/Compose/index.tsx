import { useState, useContext, useRef } from 'react'
import { useMutation } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Wand2, RotateCcw, RefreshCw, ChevronDown, ChevronRight, Pencil, Check, X, Search, StopCircle } from 'lucide-react'
import { useStore } from '../../store'
import { composeApi } from '../../api/compose'
import api from '../../api/client'
import { STATUS_META } from '../../types'
import type { Contact } from '../../types'
import EmailBadge from '../shared/EmailBadge'
import { ResumeReadyCtx } from '../../App'
import { contactDisplayName, isGenericName } from '../../lib/display'
import { useAllDrafts } from '../../hooks/useAllDrafts'

const SENT_STATUSES = ['emailed', 'followed_up', 'replied', 'interview', 'offer', 'rejected']

export default function Compose() {
  const { contacts, resume, drafts, setDrafts, setActiveTab } = useStore()
  const resumeReady = useContext(ResumeReadyCtx)
  const [showSent, setShowSent] = useState(false)
  const [search, setSearch] = useState('')
  const [bulkProgress, setBulkProgress] = useState<{ done: number; total: number } | null>(null)
  // Set by the Stop button; checked between iterations of the bulk loop.
  const bulkStopRef = useRef(false)

  // Drafts come from a shared query so Compose and Send don't each refetch them.
  const { draftsLoaded } = useAllDrafts()

  const composeMutation = useMutation({
    mutationFn: composeApi.generate,
    onSuccess: (draft) => {
      const existing = drafts[draft.contact_id] ?? []
      setDrafts(draft.contact_id, [draft, ...existing.filter(d => !d.is_followup)])
      toast.success('Email generated')
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const followupMutation = useMutation({
    mutationFn: composeApi.followUp,
    onSuccess: (draft) => {
      const existing = drafts[draft.contact_id] ?? []
      setDrafts(draft.contact_id, [draft, ...existing])
      toast.success('Follow-up ready')
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const pending = composeMutation.isPending || followupMutation.isPending

  // Generate drafts for every ungenerated contact — ONE AT A TIME with a gap.
  // Free LLM tiers (Groq) cap at ~30 requests/min; firing them in parallel (or
  // in a tight burst) trips 429 rate limits, and the provider's auto-retries then
  // amplify the storm. Sequential + a throttle gap keeps us safely under the cap.
  const generateAll = async (targets: Contact[]) => {
    // Don't start a 20-email run the health endpoint already knows will fail —
    // that would be one error toast every 2s for the whole batch.
    try {
      const { data } = await api.get<{ llm_ok?: boolean }>('/health')
      if (data.llm_ok === false) {
        toast.error('Email writing is unavailable right now — try again in a few minutes.')
        return
      }
    } catch { /* health being unreachable shouldn't block the attempt */ }

    bulkStopRef.current = false
    setBulkProgress({ done: 0, total: targets.length })
    let consecutiveFailures = 0
    try {
      for (let i = 0; i < targets.length; i++) {
        if (bulkStopRef.current) {
          toast(`Stopped after ${i} of ${targets.length}`, { icon: '⏹️' })
          break
        }
        try {
          await composeMutation.mutateAsync({ contact_id: targets[i].id, resume })
          consecutiveFailures = 0
        } catch {
          // onError already surfaced a toast for this contact. If the provider
          // is clearly down, stop instead of raining errors for the whole batch.
          if (++consecutiveFailures >= 3) {
            toast.error('Stopped — generation keeps failing. Try again in a few minutes.')
            break
          }
        }
        setBulkProgress({ done: i + 1, total: targets.length })
        if (i < targets.length - 1) await new Promise(r => setTimeout(r, 2000))
      }
    } finally {
      setBulkProgress(null)
    }
  }

  if (!resume.trim()) {
    if (!resumeReady) return (
      <div className="space-y-4 animate-pulse" aria-hidden>
        <div className="h-8 rounded w-40" style={{ background: 'var(--surface-3)' }} />
        <div className="h-4 rounded w-64" style={{ background: 'var(--surface-2)' }} />
        {[0, 1, 2].map(i => (
          <div key={i} className="card" style={{ padding: 20 }}>
            <div className="flex items-center gap-3 mb-3">
              <div className="h-4 rounded w-1/3" style={{ background: 'var(--surface-3)' }} />
              <div className="h-3 rounded w-16" style={{ background: 'var(--surface-2)' }} />
            </div>
            <div className="h-3 rounded w-2/3" style={{ background: 'var(--surface-2)' }} />
          </div>
        ))}
      </div>
    )
    return (
      <div className="text-center py-20 space-y-3">
        <div className="text-3xl">📄</div>
        <p className="text-sm font-semibold">No resume saved yet</p>
        <button onClick={() => setActiveTab('setup')} className="btn btn-primary text-sm">
          Go to Setup →
        </button>
      </div>
    )
  }

  if (contacts.length === 0) return (
    <div className="text-center py-20 space-y-3">
      <div className="text-3xl">🎯</div>
      <p className="text-sm font-semibold">No contacts yet</p>
      <button onClick={() => setActiveTab('hunt')} className="btn btn-primary text-sm">
        Hunt for contacts →
      </button>
    </div>
  )

  if (!draftsLoaded) return (
    <div className="space-y-4 animate-pulse" aria-hidden>
      <div className="flex items-start justify-between">
        <div>
          <div className="h-8 rounded w-40 mb-2" style={{ background: 'var(--surface-3)' }} />
          <div className="h-4 rounded w-56" style={{ background: 'var(--surface-2)' }} />
        </div>
        <div className="h-9 rounded-lg w-36" style={{ background: 'var(--surface-3)' }} />
      </div>
      {[0, 1, 2, 3].map(i => (
        <div key={i} className="card" style={{ padding: 20 }}>
          <div className="flex items-center gap-3 mb-3">
            <div className="h-4 rounded w-1/3" style={{ background: 'var(--surface-3)' }} />
            <div className="h-3 rounded w-16" style={{ background: 'var(--surface-2)' }} />
          </div>
          <div className="h-3 rounded w-2/3 mb-2" style={{ background: 'var(--surface-2)' }} />
          <div className="h-20 rounded-lg" style={{ background: 'var(--surface-2)' }} />
        </div>
      ))}
    </div>
  )

  // Split: new contacts (never emailed) vs already actioned
  const newContacts  = contacts.filter(c => !SENT_STATUSES.includes(c.status))
  const sentContacts = contacts.filter(c => SENT_STATUSES.includes(c.status))

  // Only count truly new + no draft as needing generation.
  // Computed from the UNFILTERED list so "Generate all (N)" stays honest
  // while a search filter is active.
  const ungenerated = newContacts.filter(
    c => !(drafts[c.id] ?? []).some(d => !d.is_followup)
  )

  // Text filter over both lists — long pipelines shouldn't mean scrolling.
  const q = search.trim().toLowerCase()
  const matches = (c: Contact) =>
    !q || [c.name, c.company, c.email, c.designation].some(v => (v || '').toLowerCase().includes(q))
  const visibleNew  = newContacts.filter(matches)
  const visibleSent = sentContacts.filter(matches)

  return (
    <div className="space-y-5">
      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold tracking-wide mb-1" style={{ fontFamily: 'var(--font-display)' }}>Compose</h1>
          <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
            {newContacts.length} new · {ungenerated.length} need drafts · {sentContacts.length} already sent
          </p>
        </div>
        {ungenerated.length > 0 && (
          <div className="flex items-center gap-2">
            <button
              onClick={() => generateAll(ungenerated)}
              disabled={pending || bulkProgress !== null}
              className="btn btn-primary text-xs flex items-center gap-2"
            >
              {bulkProgress ? (
                <><RefreshCw size={13} className="animate-spin" /> Generating {Math.min(bulkProgress.done + 1, bulkProgress.total)}/{bulkProgress.total}…</>
              ) : (
                <><Wand2 size={13} /> Generate all ({ungenerated.length})</>
              )}
            </button>
            {bulkProgress && (
              <button
                onClick={() => { bulkStopRef.current = true }}
                className="btn btn-ghost text-xs flex items-center gap-1"
                style={{ color: 'var(--text-muted)' }}
                title="Finish the current email, then stop"
              >
                <StopCircle size={13} /> Stop
              </button>
            )}
          </div>
        )}
      </div>

      {/* ── Search (only useful once the list is long) ────────────────────── */}
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

      {/* ── New contacts ────────────────────────────────────────────────── */}
      {newContacts.length === 0 ? (
        <div className="text-center py-10 space-y-2">
          <p className="text-sm font-semibold" style={{ color: 'var(--text-muted)' }}>
            All contacts have been emailed
          </p>
          <p className="text-xs" style={{ color: 'var(--text-dim)' }}>
            Hunt for more contacts or write follow-ups below
          </p>
        </div>
      ) : visibleNew.length === 0 && q ? (
        <p className="text-sm text-center py-8" style={{ color: 'var(--text-dim)' }}>
          No new contacts match “{search.trim()}”
        </p>
      ) : (
        <div className="space-y-4">
          {visibleNew.map(c => <ContactCard key={c.id} contact={c} drafts={drafts} composeMutation={composeMutation} followupMutation={followupMutation} resume={resume} />)}
        </div>
      )}

      {/* ── Already sent (collapsible) ───────────────────────────────────── */}
      {visibleSent.length > 0 && (
        <div>
          <button
            onClick={() => setShowSent(s => !s)}
            className="flex items-center gap-2 text-xs font-mono font-bold tracking-widest w-full py-2"
            style={{ color: 'var(--text-dim)' }}
          >
            {showSent ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            ALREADY SENT ({visibleSent.length})
            <span className="flex-1 border-t ml-2" style={{ borderColor: 'var(--border)' }} />
          </button>

          {showSent && (
            <div className="space-y-4 mt-2">
              {visibleSent.map(c => (
                <ContactCard
                  key={c.id}
                  contact={c}
                  drafts={drafts}
                  composeMutation={composeMutation}
                  followupMutation={followupMutation}
                  resume={resume}
                  dimmed
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Contact card ─────────────────────────────────────────────────────────────
function ContactCard({ contact: c, drafts, composeMutation, followupMutation, resume, dimmed = false }: {
  contact: Contact
  drafts: Record<number, any[]>
  composeMutation: any
  followupMutation: any
  resume: string
  dimmed?: boolean
}) {
  const { drafts: allDrafts, setDrafts } = useStore()
  const contactDrafts = drafts[c.id] ?? []
  const latest   = contactDrafts.find((d: any) => !d.is_followup)
  const followup = contactDrafts.find((d: any) => d.is_followup)
  const st = STATUS_META[c.status] ?? STATUS_META.new

  const [editing, setEditing] = useState(false)
  const [editSubject, setEditSubject] = useState('')
  const [editBody, setEditBody] = useState('')
  const [saving, setSaving] = useState(false)
  const [editingFollowup, setEditingFollowup] = useState(false)
  const [editFollowupSubject, setEditFollowupSubject] = useState('')
  const [editFollowupBody, setEditFollowupBody] = useState('')
  const [savingFollowup, setSavingFollowup] = useState(false)

  const startEdit = () => {
    setEditSubject(latest.subject)
    setEditBody(latest.body)
    setEditing(true)
  }

  const saveEdit = async () => {
    if (!editSubject.trim() || !editBody.trim()) { toast.error('Subject and body cannot be empty'); return }
    setSaving(true)
    try {
      const updated = await composeApi.editDraft(latest.id, editSubject, editBody)
      const existing = allDrafts[c.id] ?? []
      setDrafts(c.id, existing.map((d: any) => d.id === updated.id ? updated : d))
      setEditing(false)
      toast.success('Draft updated')
    } catch (e: any) {
      toast.error(e.message)
    } finally {
      setSaving(false)
    }
  }

  const startEditFollowup = () => {
    setEditFollowupSubject(followup.subject)
    setEditFollowupBody(followup.body)
    setEditingFollowup(true)
  }

  const saveFollowupEdit = async () => {
    if (!editFollowupSubject.trim() || !editFollowupBody.trim()) { toast.error('Subject and body cannot be empty'); return }
    setSavingFollowup(true)
    try {
      const updated = await composeApi.editDraft(followup.id, editFollowupSubject, editFollowupBody)
      const existing = allDrafts[c.id] ?? []
      setDrafts(c.id, existing.map((d: any) => d.id === updated.id ? updated : d))
      setEditingFollowup(false)
      toast.success('Follow-up updated')
    } catch (e: any) {
      toast.error(e.message)
    } finally {
      setSavingFollowup(false)
    }
  }

  const isGenerating =
    (composeMutation.isPending  && composeMutation.variables?.contact_id  === c.id) ||
    (followupMutation.isPending && followupMutation.variables?.contact_id === c.id)

  return (
    <div className="card" style={{ opacity: dimmed ? 0.65 : 1 }}>
      {/* ── Header ── */}
      <div className="flex items-start justify-between mb-4">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-sm">{contactDisplayName(c)}</span>
            {isGenericName(c.name) && (
              <span className="text-xs font-mono" style={{ color: 'var(--text-muted)' }}>{c.email}</span>
            )}
            <span className="badge" style={{ background: st.bg, color: st.color, fontSize: '9px' }}>
              {st.label}
            </span>
            <EmailBadge status={c.email_status} confidence={c.confidence} />
          </div>
          <div className="text-xs font-mono mt-0.5" style={{ color: 'var(--text-dim)' }}>
            {c.designation} · {c.company}
          </div>
        </div>

        <div className="flex gap-2 flex-shrink-0">
          {/* A follow-up only makes sense once the first email actually went
              out — for un-emailed contacts the button would generate a "just
              checking in" to someone who never got anything. */}
          {latest && SENT_STATUSES.includes(c.status) && (
            <button
              onClick={() => followupMutation.mutate({
                contact_id: c.id,
                original_email: `SUBJECT: ${latest.subject}\n\nBODY:\n${latest.body}`,
              })}
              disabled={isGenerating}
              className="btn btn-ghost text-xs flex items-center gap-1"
              style={{ color: '#6f5ae0', borderColor: 'rgba(111,90,224,0.3)' }}
            >
              ↩ Follow Up
            </button>
          )}
          <button
            onClick={() => composeMutation.mutate({ contact_id: c.id, resume })}
            disabled={isGenerating}
            className="btn btn-ghost text-xs flex items-center gap-1"
          >
            {isGenerating ? (
              <><RefreshCw size={11} className="animate-spin" /> Writing…</>
            ) : latest ? (
              <><RotateCcw size={11} /> Regen</>
            ) : (
              <><Wand2 size={11} /> Generate</>
            )}
          </button>
        </div>
      </div>

      {/* ── Draft ── */}
      {latest && !editing && (
        <div className="rounded-lg p-3 space-y-2 relative group/draft" style={{ background: 'var(--surface-2)' }}>
          <button
            onClick={startEdit}
            title="Edit draft"
            className="absolute top-2 right-2 flex items-center gap-1 text-xs px-2 py-1 rounded opacity-0 group-hover/draft:opacity-100 transition-opacity"
            style={{ background: 'rgba(226,96,63,0.10)', color: 'var(--accent)' }}
          >
            <Pencil size={11} /> Edit
          </button>
          <div>
            <div className="text-xs font-bold font-mono mb-1" style={{ color: 'var(--text-dim)' }}>SUBJECT</div>
            <div className="text-sm font-medium pr-16">{latest.subject}</div>
          </div>
          <div>
            <div className="text-xs font-bold font-mono mb-1" style={{ color: 'var(--text-dim)' }}>BODY</div>
            <div className="text-sm whitespace-pre-wrap" style={{ color: 'var(--text-muted)', lineHeight: '1.75' }}>
              {latest.body}
            </div>
          </div>
        </div>
      )}

      {/* ── Draft (editing) ── */}
      {latest && editing && (
        <div className="rounded-lg p-3 space-y-3" style={{ background: 'var(--surface-2)', border: '1px solid rgba(226,96,63,0.3)' }}>
          <div>
            <div className="text-xs font-bold font-mono mb-1" style={{ color: 'var(--text-dim)' }}>SUBJECT</div>
            <input
              value={editSubject}
              onChange={e => setEditSubject(e.target.value)}
              className="input text-sm w-full"
            />
          </div>
          <div>
            <div className="text-xs font-bold font-mono mb-1" style={{ color: 'var(--text-dim)' }}>BODY</div>
            <textarea
              value={editBody}
              onChange={e => setEditBody(e.target.value)}
              rows={Math.min(18, Math.max(6, editBody.split('\n').length + 1))}
              className="input text-sm w-full font-sans"
              style={{ lineHeight: '1.7', resize: 'vertical' }}
            />
          </div>
          <div className="flex gap-2">
            <button
              onClick={saveEdit}
              disabled={saving}
              className="btn text-xs flex items-center gap-1.5 font-semibold"
              style={{ background: 'rgba(63,143,67,0.12)', borderColor: 'rgba(63,143,67,0.35)', color: '#3f8f43' }}
            >
              <Check size={12} /> {saving ? 'Saving…' : 'Save'}
            </button>
            <button
              onClick={() => setEditing(false)}
              disabled={saving}
              className="btn btn-ghost text-xs flex items-center gap-1.5"
              style={{ color: 'var(--text-muted)' }}
            >
              <X size={12} /> Cancel
            </button>
          </div>
        </div>
      )}

      {/* ── Follow-up draft ── */}
      {followup && !editingFollowup && (
        <div
          className="rounded-lg p-3 mt-3 relative group/followup"
          style={{ background: 'rgba(111,90,224,0.06)', border: '1px solid rgba(111,90,224,0.2)' }}
        >
          <button
            onClick={startEditFollowup}
            title="Edit follow-up"
            className="absolute top-2 right-2 flex items-center gap-1 text-xs px-2 py-1 rounded opacity-0 group-hover/followup:opacity-100 transition-opacity"
            style={{ background: 'rgba(111,90,224,0.12)', color: '#6f5ae0' }}
          >
            <Pencil size={11} /> Edit
          </button>
          <div className="text-xs font-bold font-mono mb-2" style={{ color: '#6f5ae0' }}>FOLLOW-UP DRAFT</div>
          <div className="text-xs font-medium mb-1">{followup.subject}</div>
          <div className="text-sm" style={{ color: 'var(--text-muted)', lineHeight: '1.75' }}>{followup.body}</div>
        </div>
      )}

      {followup && editingFollowup && (
        <div className="rounded-lg p-3 mt-3 space-y-3" style={{ background: 'rgba(111,90,224,0.06)', border: '1px solid rgba(111,90,224,0.4)' }}>
          <div className="text-xs font-bold font-mono mb-1" style={{ color: '#6f5ae0' }}>FOLLOW-UP DRAFT</div>
          <div>
            <div className="text-xs font-bold font-mono mb-1" style={{ color: 'var(--text-dim)' }}>SUBJECT</div>
            <input
              value={editFollowupSubject}
              onChange={e => setEditFollowupSubject(e.target.value)}
              className="input text-sm w-full"
            />
          </div>
          <div>
            <div className="text-xs font-bold font-mono mb-1" style={{ color: 'var(--text-dim)' }}>BODY</div>
            <textarea
              value={editFollowupBody}
              onChange={e => setEditFollowupBody(e.target.value)}
              rows={Math.min(10, Math.max(4, editFollowupBody.split('\n').length + 1))}
              className="input text-sm w-full font-sans"
              style={{ lineHeight: '1.7', resize: 'vertical' }}
            />
          </div>
          <div className="flex gap-2">
            <button
              onClick={saveFollowupEdit}
              disabled={savingFollowup}
              className="btn text-xs flex items-center gap-1.5 font-semibold"
              style={{ background: 'rgba(111,90,224,0.12)', borderColor: 'rgba(111,90,224,0.35)', color: '#6f5ae0' }}
            >
              <Check size={12} /> {savingFollowup ? 'Saving…' : 'Save'}
            </button>
            <button
              onClick={() => setEditingFollowup(false)}
              disabled={savingFollowup}
              className="btn btn-ghost text-xs flex items-center gap-1.5"
              style={{ color: 'var(--text-muted)' }}
            >
              <X size={12} /> Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
