import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Search, Trash2, Download, ShieldCheck } from 'lucide-react'
import { useStore } from '../../store'
import { contactsApi } from '../../api/contacts'
import { verifyApi } from '../../api/verify'
import ContactCard from './ContactCard'
import ConfirmDialog from '../shared/ConfirmDialog'
import type { ContactStatus } from '../../types'

const STATUS_FILTERS: Array<ContactStatus | 'all'> = [
  'all', 'new', 'emailed', 'followed_up', 'replied', 'interview', 'offer', 'rejected', 'bounced',
]

// ── Dynamic query suggestions ────────────────────────────────────────────────
// Chips adapt to the user's résumé: skills we spot become tailored queries,
// topped up with rotating general ones so the row always feels fresh.
const SKILL_QUERIES: Array<[pattern: RegExp, query: string]> = [
  [/\breact\b/i,                 'react engineer hiring'],
  [/\b(golang|go)\b/i,           'golang hiring'],
  [/\bpython\b/i,                'python backend hiring'],
  [/\btypescript\b/i,            'typescript engineer hiring'],
  [/\bnode(\.js)?\b/i,           'node backend hiring'],
  [/\bjava\b(?!script)/i,        'java engineer hiring'],
  [/\b(kubernetes|devops|terraform|docker)\b/i, 'devops kubernetes hiring'],
  [/\b(data engineer|spark|airflow|etl)\b/i,    'data engineer hiring'],
  [/\b(machine learning|pytorch|tensorflow|llm)\b/i, 'machine learning engineer hiring'],
  [/\b(android|kotlin)\b/i,      'android developer hiring'],
  [/\b(ios|swift)\b/i,           'ios developer hiring'],
  [/\brust\b/i,                  'rust engineer hiring'],
  [/\bfullstack|full-stack|full stack\b/i, 'fullstack engineer remote'],
  [/\bfrontend|front-end\b/i,    'frontend developer hiring'],
  [/\bbackend|back-end\b/i,      'backend engineer hiring'],
]
const GENERAL_QUERIES = [
  'founding engineer', 'software engineer hiring india', 'senior engineer remote',
  'startup hiring engineers', 'platform engineer hiring', 'sre hiring',
]

function buildChips(resume: string): string[] {
  const matched = SKILL_QUERIES.filter(([re]) => re.test(resume)).map(([, q]) => q)
  // Shuffle the general pool so returning users see variety
  const extras = [...GENERAL_QUERIES].sort(() => Math.random() - 0.5)
  const chips: string[] = []
  for (const q of [...matched, ...extras]) {
    if (!chips.includes(q)) chips.push(q)
    if (chips.length >= 6) break
  }
  return chips
}

// Honest, specific empty-state copy based on what the hunt actually found.
function emptyHuntMessage(query: string, found: number, duplicates: number): string {
  if (duplicates > 0)
    return `Every match for "${query}" is already in your list (${duplicates} contact${duplicates > 1 ? 's' : ''}). Try a different query.`
  if (found > 0)
    return `Found ${found} lead${found > 1 ? 's' : ''} hiring for "${query}", but no reachable email address — larger companies route everything through application portals. Startup names ("Linear", "Supabase") and specific roles ("react engineer remote") work best.`
  return `No matches for "${query}". Try a role query like "react engineer remote", or a specific startup name (e.g. "Linear", "Supabase").`
}

function SkeletonCard() {
  return (
    <div className="card animate-pulse" aria-hidden>
      <div className="flex items-center gap-3 mb-3">
        <div className="w-9 h-9 rounded-full" style={{ background: 'var(--surface-3)' }} />
        <div className="space-y-2 flex-1">
          <div className="h-3 rounded w-2/3" style={{ background: 'var(--surface-3)' }} />
          <div className="h-2 rounded w-1/3" style={{ background: 'var(--surface-2)' }} />
        </div>
      </div>
      <div className="h-2 rounded w-1/2 mb-2" style={{ background: 'var(--surface-2)' }} />
      <div className="h-2 rounded w-3/4" style={{ background: 'var(--surface-2)' }} />
    </div>
  )
}

export default function Hunt() {
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<ContactStatus | 'all'>('all')
  const [verifying, setVerifying] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [showClearConfirm, setShowClearConfirm] = useState(false)
  // Hunt state lives in the store: switching tabs mid-hunt no longer kills it —
  // the request finishes in the background and this tab restores progress/results.
  const {
    setContacts, contacts, clearContacts, upsertContact, resume,
    hunting, huntStage, huntResults, huntInfo, runHunt, clearHunt,
  } = useStore()
  const qc = useQueryClient()

  // Suggestions personalised from the résumé, stable for this visit.
  const chips = useMemo(() => buildChips(resume), [resume])

  const handleVerify = async () => {
    setVerifying(true)
    try {
      const res = await verifyApi.run([])   // verify all not-yet-verified
      const fresh = await contactsApi.list()
      fresh.forEach(c => upsertContact(c))
      qc.invalidateQueries({ queryKey: ['contacts'] })
      if (res.results.length === 0) {
        toast('All contacts already verified', { icon: '✅' })
      } else {
        toast.success(`Verified ${res.results.length} · ${res.valid} valid, ${res.risky} risky, ${res.invalid} invalid`)
      }
    } catch (e: any) {
      toast.error(e.message)
    } finally {
      setVerifying(false)
    }
  }

  // Delete everything server-side FIRST, then clear the local store. (Clearing
  // only the store looks like it worked until the next refetch restores it all.)
  const handleClearAll = async () => {
    setClearing(true)
    try {
      await contactsApi.deleteAll()
      clearContacts()
      clearHunt()
      qc.invalidateQueries({ queryKey: ['contacts'] })
      setShowClearConfirm(false)
      toast.success('All contacts removed')
    } catch (e: any) {
      toast.error(e.message)
    } finally {
      setClearing(false)
    }
  }

  // TanStack Query v5 — useEffect for side effects on query data
  const { data: remoteContacts } = useQuery({
    queryKey: ['contacts'],
    queryFn:  contactsApi.list,
  })
  useEffect(() => {
    if (remoteContacts) setContacts(remoteContacts)
  }, [remoteContacts]) // eslint-disable-line react-hooks/exhaustive-deps

  const doHunt = (q: string) => {
    if (!q.trim() || hunting) return
    void runHunt(q)   // store-level: keeps running if the user leaves this tab
  }

  const exportCSV = () => {
    const header = 'Name,Email,Designation,Company,Status'
    // Guard against CSV formula injection: a field starting with = + - @ can be
    // executed as a formula when opened in Excel/Sheets — prefix it with a quote.
    const cell = (v: string) => {
      let s = v ?? ''
      if (/^[=+\-@]/.test(s)) s = "'" + s
      return `"${s.replace(/"/g, '""')}"`
    }
    const rows = contacts.map(c =>
      [c.name, c.email, c.designation, c.company, c.status].map(cell).join(',')
    )
    const blob = new Blob([[header, ...rows].join('\n')], { type: 'text/csv' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href = url
    a.download = `coldreach-${new Date().toISOString().slice(0,10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  // After a hunt: show only results for that query.
  // Before any hunt: show all saved contacts.
  const displayList = huntResults ?? contacts
  const filtered = statusFilter === 'all'
    ? displayList
    : displayList.filter((c: any) => c.status === statusFilter)

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-wide mb-1" style={{ fontFamily: 'var(--font-display)' }}>
          Hunt Contacts
        </h1>
        <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
          Type a role, skill, or company — ColdReach finds hiring managers, recruiters,
          and founders who are actively hiring right now, each with a reachable email.
        </p>
      </div>

      {/* ── Search bar ──────────────────────────────────────────────── */}
      <div className="flex gap-2">
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && doHunt(query)}
          placeholder="golang, react engineer, python backend…"
          className="input flex-1"
          aria-label="Hunt query"
        />
        <button
          onClick={() => doHunt(query)}
          disabled={!query.trim() || hunting}
          className="btn btn-primary flex items-center gap-2"
        >
          <Search size={14} />
          {hunting ? 'Hunting…' : 'Hunt'}
        </button>
      </div>

      {/* ── Query chips (personalised from the résumé) ───────────────── */}
      <div className="flex flex-wrap gap-2">
        {chips.map(chip => (
          <button
            key={chip}
            onClick={() => { setQuery(chip); doHunt(chip) }}
            disabled={hunting}
            className="text-xs px-3 py-1.5 rounded-full border font-mono transition-colors hover:border-accent"
            style={{ borderColor: 'var(--border)', color: 'var(--text-muted)', opacity: hunting ? 0.5 : 1 }}
          >
            {chip}
          </button>
        ))}
      </div>

      {/* ── Live hunt progress ───────────────────────────────────────── */}
      {hunting && (
        <div className="space-y-3" aria-live="polite">
          <div className="flex items-center gap-2.5 text-sm" style={{ color: 'var(--text-muted)' }}>
            <span
              className="w-4 h-4 rounded-full border-2 border-t-transparent animate-spin flex-shrink-0"
              style={{ borderColor: 'var(--border-strong)', borderTopColor: 'var(--accent)' }}
            />
            {huntStage || 'Searching…'} <span style={{ color: 'var(--text-dim)' }}>— feel free to browse other tabs, this keeps running</span>
          </div>
          <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
            <SkeletonCard /><SkeletonCard /><SkeletonCard />
          </div>
        </div>
      )}

      {/* ── Toolbar: filters + actions ───────────────────────────────── */}
      {!hunting && displayList.length > 0 && (
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex gap-2 flex-wrap">
            {STATUS_FILTERS.map(s => {
              const count = s === 'all' ? displayList.length : displayList.filter((c: any) => c.status === s).length
              if (s !== 'all' && count === 0) return null
              return (
                <button
                  key={s}
                  onClick={() => setStatusFilter(s)}
                  className="text-xs px-3 py-1 rounded-full font-mono border transition-colors"
                  style={{
                    borderColor: statusFilter === s ? 'var(--accent)'      : 'var(--border)',
                    color:       statusFilter === s ? 'var(--accent)'      : 'var(--text-dim)',
                    background:  statusFilter === s ? 'var(--accent-dim)'  : 'transparent',
                  }}
                >
                  {s.replace('_', ' ')} ({count})
                </button>
              )
            })}
          </div>
          <div className="flex gap-2">
            <button
              onClick={handleVerify}
              disabled={verifying}
              title="Re-check whether each email address can actually receive mail"
              className="btn btn-ghost flex items-center gap-1 text-xs"
              style={{ color: '#3f8f43', borderColor: 'rgba(63,143,67,0.3)' }}
            >
              <ShieldCheck size={12} /> {verifying ? 'Verifying…' : 'Verify'}
            </button>
            <button onClick={exportCSV} className="btn btn-ghost flex items-center gap-1 text-xs">
              <Download size={12} /> CSV
            </button>
            <button
              onClick={() => setShowClearConfirm(true)}
              className="btn btn-ghost flex items-center gap-1 text-xs"
              style={{ color: '#d2483a' }}
            >
              <Trash2 size={12} /> Clear all
            </button>
          </div>
        </div>
      )}

      {/* ── Clear-all confirmation ───────────────────────────────────── */}
      {showClearConfirm && (
        <ConfirmDialog
          title={`Delete all ${contacts.length} contacts?`}
          confirmLabel="Delete everything"
          danger
          busy={clearing}
          onConfirm={handleClearAll}
          onCancel={() => setShowClearConfirm(false)}
        >
          <p>
            This permanently removes every contact and their drafts from your
            account. It can't be undone — export a CSV first if you want a backup.
          </p>
        </ConfirmDialog>
      )}

      {/* ── Result context label ─────────────────────────────────────── */}
      {!hunting && huntResults !== null && huntInfo && (
        <p className="text-xs flex items-baseline gap-2 flex-wrap" style={{ color: 'var(--text-muted)', lineHeight: 1.6 }}>
          <span>
            {huntResults.length > 0
              ? `Showing ${huntResults.length} new contact${huntResults.length !== 1 ? 's' : ''} found for "${huntInfo.query}"`
              : emptyHuntMessage(huntInfo.query, huntInfo.found, huntInfo.duplicates)}
          </span>
          {contacts.length > 0 && (
            <button
              onClick={clearHunt}
              className="font-semibold flex-shrink-0"
              style={{ color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer', fontSize: 12 }}
            >
              Show all {contacts.length} contacts →
            </button>
          )}
        </p>
      )}

      {/* ── Contact grid ─────────────────────────────────────────────── */}
      {!hunting && (filtered.length > 0 ? (
        <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
          {filtered.map(c => <ContactCard key={c.id} contact={c} />)}
        </div>
      ) : displayList.length === 0 ? (
        <div
          className="rounded-xl border p-14 text-center"
          style={{ borderColor: 'var(--border)', background: 'var(--surface-1)' }}
        >
          <div className="text-2xl mb-3">🎯</div>
          <p className="text-sm font-semibold mb-1">No contacts yet</p>
          <p className="text-xs font-mono" style={{ color: 'var(--text-dim)' }}>
            Try "golang hiring" or "react engineer hiring" above
          </p>
        </div>
      ) : (
        <div
          className="rounded-xl border p-10 text-center"
          style={{ borderColor: 'var(--border)', background: 'var(--surface-1)' }}
        >
          <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
            No contacts with status “{String(statusFilter).replace('_', ' ')}”.
          </p>
        </div>
      ))}
    </div>
  )
}
