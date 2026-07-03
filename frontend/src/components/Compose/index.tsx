import { useEffect, useState, useContext } from 'react'
import { useMutation } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Wand2, RotateCcw, RefreshCw, ChevronDown, ChevronRight, Pencil, Check, X } from 'lucide-react'
import { useStore } from '../../store'
import { composeApi } from '../../api/compose'
import { STATUS_META } from '../../types'
import type { Contact } from '../../types'
import { ResumeReadyCtx } from '../../App'

const SENT_STATUSES = ['emailed', 'followed_up', 'replied', 'interview', 'offer', 'rejected']

function EmailBadge({ status, confidence }: { status?: string; confidence?: number }) {
  if (!status || status === 'unknown') return null
  const meta: Record<string, { label: string; color: string; bg: string; sym: string }> = {
    valid:   { label: 'verified', sym: '✓', color: '#3f8f43', bg: 'rgba(63,143,67,.12)'  },
    risky:   { label: 'risky',    sym: '~', color: '#c47d1e', bg: 'rgba(196,125,30,.12)' },
    invalid: { label: 'invalid',  sym: '✗', color: '#d2483a', bg: 'rgba(210,72,58,.12)'  },
  }
  const m = meta[status] ?? { label: status, sym: '?', color: '#8a7f70', bg: 'rgba(138,127,112,.12)' }
  return (
    <span className="badge inline-flex items-center gap-0.5" style={{ background: m.bg, color: m.color, fontSize: '9px', whiteSpace: 'nowrap' }}>
      {m.sym} {m.label}{confidence != null ? ` · ${confidence}%` : ''}
    </span>
  )
}

export default function Compose() {
  const { contacts, resume, drafts, setDrafts, setActiveTab } = useStore()
  const resumeReady = useContext(ResumeReadyCtx)
  const [showSent, setShowSent] = useState(false)

  // Restore drafts from backend on mount
  useEffect(() => {
    contacts
      .filter(c => !(drafts[c.id]?.length))
      .forEach(c => {
        composeApi.getDrafts(c.id).then(d => {
          if (d.length > 0) setDrafts(c.id, d)
        }).catch(() => {})
      })
  }, [contacts.length])

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

  if (!resume.trim()) {
    if (!resumeReady) return (
      <div className="text-center py-20">
        <div className="w-6 h-6 mx-auto rounded-full border-2 border-t-transparent animate-spin" style={{ borderColor: 'var(--border)', borderTopColor: 'var(--accent)' }} />
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

  // Split: new contacts (never emailed) vs already actioned
  const newContacts  = contacts.filter(c => !SENT_STATUSES.includes(c.status))
  const sentContacts = contacts.filter(c => SENT_STATUSES.includes(c.status))

  // Only count truly new + no draft as needing generation
  const ungenerated = newContacts.filter(
    c => !(drafts[c.id] ?? []).some(d => !d.is_followup)
  )

  return (
    <div className="space-y-5">
      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-wide mb-1" style={{ fontFamily: 'var(--font-display)' }}>Compose</h1>
          <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
            {newContacts.length} new · {ungenerated.length} need drafts · {sentContacts.length} already sent
          </p>
        </div>
        {ungenerated.length > 0 && (
          <button
            onClick={() => {
              ungenerated.forEach((c, i) =>
                setTimeout(() => composeMutation.mutate({ contact_id: c.id, resume }), i * 500)
              )
            }}
            disabled={pending}
            className="btn btn-primary text-xs flex items-center gap-2"
          >
            <Wand2 size={13} />
            Generate all ({ungenerated.length})
          </button>
        )}
      </div>

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
      ) : (
        <div className="space-y-4">
          {newContacts.map(c => <ContactCard key={c.id} contact={c} drafts={drafts} composeMutation={composeMutation} followupMutation={followupMutation} resume={resume} />)}
        </div>
      )}

      {/* ── Already sent (collapsible) ───────────────────────────────────── */}
      {sentContacts.length > 0 && (
        <div>
          <button
            onClick={() => setShowSent(s => !s)}
            className="flex items-center gap-2 text-xs font-mono font-bold tracking-widest w-full py-2"
            style={{ color: 'var(--text-dim)' }}
          >
            {showSent ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            ALREADY SENT ({sentContacts.length})
            <span className="flex-1 border-t ml-2" style={{ borderColor: 'var(--border)' }} />
          </button>

          {showSent && (
            <div className="space-y-4 mt-2">
              {sentContacts.map(c => (
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
      toast.error(e.response?.data?.detail ?? e.message)
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
      toast.error(e.response?.data?.detail ?? e.message)
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
            <span className="font-medium text-sm">{c.name}</span>
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
          {latest && (
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
