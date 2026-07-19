import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Search, Trash2, Download, CheckSquare, Check, X } from 'lucide-react'
import { useStore } from '../../store'
import { contactsApi } from '../../api/contacts'
import { useContacts } from '../../hooks/useContacts'
import { huntApi } from '../../api/hunt'
import ContactCard from './ContactCard'
import ConfirmDialog from '../shared/ConfirmDialog'
import ContactDrawer from '../shared/ContactDrawer'
import { STATUS_META, type Contact, type ContactStatus } from '../../types'

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

// Target-role filter. Value maps to the backend's role families; '' = no filter.
// When set, the hunt keeps that family + gatekeepers (founders, recruiters) and
// drops off-target people (e.g. no plain engineers on a management search).
const ROLE_OPTIONS: Array<[value: string, label: string]> = [
  ['',             'Any role'],
  ['engineering',  'Engineering'],
  ['management',   'Management / Leadership'],
  ['founder_exec', 'Founder / Exec'],
  ['recruiting',   'Recruiting / Talent'],
  ['product',      'Product'],
  ['design',       'Design'],
  ['data',         'Data / ML'],
]

function buildChips(resume: string, hiringCompanies: string[]): string[] {
  const matched = SKILL_QUERIES.filter(([re]) => re.test(resume)).map(([, q]) => q)
  // Shuffle the general pool so returning users see variety
  const extras = [...GENERAL_QUERIES].sort(() => Math.random() - 0.5)
  const chips: string[] = []
  // Live currently-hiring companies first (clicking hunts that company
  // directly — the highest-yield query type), then resume-personalised roles.
  const companies = [...hiringCompanies].sort(() => Math.random() - 0.5).slice(0, 3)
  for (const q of [...companies, ...matched, ...extras]) {
    if (!chips.includes(q)) chips.push(q)
    if (chips.length >= 7) break
  }
  return chips
}

// Honest, specific empty-state copy based on what the hunt actually found.
function emptyHuntMessage(query: string, found: number, duplicates: number, roleFiltered: number): string {
  if (duplicates > 0)
    return `Every match for "${query}" is already in your list (${duplicates} contact${duplicates > 1 ? 's' : ''}). Try a different query.`
  // Role filter accounted for the misses — don't blame "no reachable email".
  if (roleFiltered > 0)
    return `Found ${roleFiltered} reachable lead${roleFiltered > 1 ? 's' : ''} for "${query}", but none matched your role filter (founders & recruiters are always kept). Switch to "Any role" or pick a different role to see them.`
  if (found > 0)
    return `Found ${found} lead${found > 1 ? 's' : ''} hiring for "${query}", but no reachable email address — larger companies route everything through application portals. Startup names ("Linear", "Supabase") and specific roles ("react engineer remote") work best.`
  return `No matches for "${query}". Try a role query like "react engineer remote", or a specific startup name (e.g. "Linear", "Supabase").`
}

// The pipeline stages of the "LIVE PROGRESS" panel. The store only exposes
// the current stage as a display string (see HUNT_STAGES in store/index.ts),
// so each stage here carries a matcher against that string to derive which
// stages are done / active / pending. An unrecognised string (e.g. a future
// store label) falls back to stage 0 rather than breaking the panel.
const PIPELINE_STAGES: Array<{ label: string; match: RegExp }> = [
  { label: 'Finding companies hiring',  match: /searching for companies/i },
  { label: 'Matching decision-makers',  match: /matching people/i },
  { label: 'Checking email addresses',  match: /finding and checking email/i },
  { label: 'Putting results together',  match: /almost there/i },
]

function StageDot({ state }: { state: 'done' | 'active' | 'pending' }) {
  if (state === 'done') {
    return <Check size={12} strokeWidth={3} style={{ color: 'var(--success-text)', flexShrink: 0 }} aria-hidden />
  }
  if (state === 'active') {
    return (
      <span
        className="w-3 h-3 rounded-full border-2 border-t-transparent animate-spin flex-shrink-0"
        style={{ borderColor: 'var(--border-strong)', borderTopColor: 'var(--accent)' }}
        aria-hidden
      />
    )
  }
  return (
    <span
      className="w-2 h-2 rounded-full flex-shrink-0"
      style={{ background: 'var(--border-strong)', margin: '0 2px' }}
      aria-hidden
    />
  )
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
  const [role, setRole]   = useState('')   // target-role filter ('' = any)
  const [statusFilter, setStatusFilter] = useState<ContactStatus | 'all'>('all')
  const [clearing, setClearing] = useState(false)
  const [showClearConfirm, setShowClearConfirm] = useState(false)
  const [selectMode, setSelectMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [bulkBusy, setBulkBusy] = useState(false)
  // Detail drawer target. Stored as an id (not a Contact snapshot) so status
  // and note edits made inside the drawer show up live — the object is
  // re-derived from the store on every render.
  const [drawerId, setDrawerId] = useState<number | null>(null)
  // Hunt state lives in the store: switching tabs mid-hunt no longer kills it —
  // the request finishes in the background and this tab restores progress/results.
  const {
    contacts, clearContacts, upsertContact, removeContact, resume,
    hunting, huntStage, huntResults, huntInfo, runHunt, cancelHunt, clearHunt, removeHuntResult,
  } = useStore()
  const qc = useQueryClient()

  // Live "who's hiring right now" companies for the suggestion chips —
  // cached server-side, refreshed at most every 15 min.
  const { data: suggestions } = useQuery({
    queryKey: ['hunt-suggestions'],
    queryFn: huntApi.suggestions,
    staleTime: 15 * 60_000,
    retry: false,
  })

  // Suggestions: live hiring companies + résumé-personalised role queries.
  const hiringCompanies = suggestions?.hiring_companies ?? []
  const chips = useMemo(
    () => buildChips(resume, hiringCompanies),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [resume, hiringCompanies.join('|')],
  )

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

  // Contacts come from the shared query (also used by App and Today) — one
  // fetch serves every tab instead of each firing its own.
  useContacts()

  const doHunt = (q: string) => {
    if (!q.trim() || hunting) return
    void runHunt(q, role)   // store-level: keeps running if the user leaves this tab
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
    // Export what the user is LOOKING at — an active status filter narrows the
    // export too ("export what I see"), and the filename says so.
    const rows = filtered.map(c =>
      [c.name, c.email, c.designation, c.company, c.status].map(cell).join(',')
    )
    // ﻿ = UTF-8 BOM: without it Excel decodes as ANSI and garbles
    // non-ASCII names (é, ñ, Indian scripts).
    const blob = new Blob(['﻿', [header, ...rows].join('\n')], { type: 'text/csv;charset=utf-8' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href = url
    const scope = statusFilter === 'all' ? '' : `-${statusFilter}`
    a.download = `coldreach${scope}-${new Date().toISOString().slice(0,10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const toggleSelect = (id: number) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const selectAll = () => setSelectedIds(new Set(filtered.map(c => c.id)))
  const deselectAll = () => setSelectedIds(new Set())

  const exitSelectMode = () => {
    setSelectMode(false)
    setSelectedIds(new Set())
  }

  const bulkSetStatus = async (status: ContactStatus) => {
    setBulkBusy(true)
    let ok = 0
    for (const id of selectedIds) {
      try {
        const updated = await contactsApi.setStatus(id, status)
        upsertContact(updated)
        ok++
      } catch { /* skip */ }
    }
    qc.invalidateQueries({ queryKey: ['contacts'] })
    toast.success(`Updated ${ok} contact${ok !== 1 ? 's' : ''} to ${STATUS_META[status].label}`)
    exitSelectMode()
    setBulkBusy(false)
  }

  const bulkDelete = async () => {
    setBulkBusy(true)
    let ok = 0
    for (const id of selectedIds) {
      try {
        await contactsApi.delete(id)
        removeContact(id)
        removeHuntResult(id)
        ok++
      } catch { /* skip */ }
    }
    qc.invalidateQueries({ queryKey: ['contacts'] })
    toast.success(`Removed ${ok} contact${ok !== 1 ? 's' : ''}`)
    exitSelectMode()
    setBulkBusy(false)
  }

  // After a hunt: show only results for that query.
  // Before any hunt: show all saved contacts.
  const displayList = huntResults ?? contacts
  const filtered = statusFilter === 'all'
    ? displayList
    : displayList.filter((c: any) => c.status === statusFilter)

  // Live drawer contact — falls back to the saved list so the drawer keeps
  // working when a hunt-result contact is also (or only) in `contacts`, and
  // closes itself (derives to null) if the contact gets deleted.
  const drawerContact: Contact | null = drawerId === null
    ? null
    : displayList.find(c => c.id === drawerId) ?? contacts.find(c => c.id === drawerId) ?? null

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
      <div className="flex flex-col sm:flex-row gap-2">
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && doHunt(query)}
          placeholder="golang, react engineer, python backend…"
          className="input flex-1"
          aria-label="Hunt query"
        />
        <select
          value={role}
          onChange={e => setRole(e.target.value)}
          disabled={hunting}
          className="input sm:w-auto"
          aria-label="Target role"
          title="Only show people in this role (plus founders & recruiters)"
        >
          {ROLE_OPTIONS.map(([value, label]) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>
        <button
          onClick={() => doHunt(query)}
          disabled={!query.trim() || hunting}
          className="btn btn-primary flex items-center gap-2 justify-center"
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
            className="relative text-xs px-3 py-1.5 rounded-full border font-mono transition-colors hover:border-accent before:absolute before:-inset-y-1.5 before:inset-x-0 before:content-['']"
            style={{ borderColor: 'var(--border)', color: 'var(--text-muted)', opacity: hunting ? 0.5 : 1 }}
          >
            {chip}
          </button>
        ))}
      </div>

      {/* ── Live hunt progress ───────────────────────────────────────── */}
      {hunting && (() => {
        const activeIdx = Math.max(0, PIPELINE_STAGES.findIndex(s => s.match.test(huntStage)))
        return (
          <div className="space-y-3">
            <div className="card" style={{ padding: '15px 18px' }}>
              <div className="flex items-center justify-between gap-3 mb-3">
                <span className="text-[10px] font-mono font-bold tracking-widest" style={{ color: 'var(--text-muted)' }}>
                  LIVE PROGRESS
                </span>
                <button
                  onClick={cancelHunt}
                  className="btn btn-ghost text-xs flex items-center gap-1"
                  style={{ color: 'var(--text-muted)' }}
                >
                  <X size={11} /> Cancel
                </button>
              </div>
              <div className="grid gap-2" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(210px, 1fr))' }}>
                {PIPELINE_STAGES.map((stage, i) => {
                  const state = i < activeIdx ? 'done' : i === activeIdx ? 'active' : 'pending'
                  return (
                    <div
                      key={stage.label}
                      className="flex items-center gap-2.5 rounded-[10px] border"
                      style={{
                        padding: '8px 12px',
                        borderColor: 'var(--border)',
                        background: state === 'done'
                          ? 'color-mix(in srgb, var(--success) 8%, transparent)'
                          : state === 'active' ? 'var(--accent-dim)' : 'transparent',
                        transition: 'background .3s',
                      }}
                    >
                      <StageDot state={state} />
                      <span
                        className="text-xs font-semibold"
                        style={{ color: state === 'pending' ? 'var(--text-dim)' : 'var(--text)' }}
                      >
                        {stage.label}
                      </span>
                      <span
                        className="ml-auto text-[11px] font-mono"
                        style={{ color: state === 'done' ? 'var(--success-text)' : 'var(--text-dim)' }}
                      >
                        {state === 'done' ? 'done' : state === 'active' ? '…' : ''}
                      </span>
                    </div>
                  )
                })}
              </div>
              {/* Screen readers hear the store's own stage sentence as it changes. */}
              <span className="sr-only" aria-live="polite">{huntStage}</span>
              <p className="text-xs mt-3 mb-0" style={{ color: 'var(--text-muted)' }}>
                Working — results land here the moment they're verified.{' '}
                <span style={{ color: 'var(--text-dim)' }}>Feel free to browse other tabs — this keeps running.</span>
              </p>
            </div>
            <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
              <SkeletonCard /><SkeletonCard /><SkeletonCard />
            </div>
          </div>
        )
      })()}

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
                  className="relative text-xs px-3 py-1 rounded-full font-mono border transition-colors tnum before:absolute before:-inset-y-2 before:inset-x-0 before:content-['']"
                  style={{
                    borderColor: statusFilter === s ? 'var(--accent)'      : 'var(--border)',
                    color:       statusFilter === s ? 'var(--accent-text)' : 'var(--text-muted)',
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
              onClick={() => selectMode ? exitSelectMode() : setSelectMode(true)}
              className="btn btn-ghost flex items-center gap-1 text-xs"
              style={selectMode ? { color: 'var(--accent)', borderColor: 'var(--accent)', background: 'var(--accent-dim)' } : {}}
            >
              {selectMode ? <X size={12} /> : <CheckSquare size={12} />}
              {selectMode ? 'Cancel' : 'Select'}
            </button>
            <button onClick={exportCSV} className="btn btn-ghost flex items-center gap-1 text-xs">
              <Download size={12} /> CSV
            </button>
            <button
              onClick={() => setShowClearConfirm(true)}
              className="btn btn-ghost flex items-center gap-1 text-xs"
              style={{ color: 'var(--danger-text)' }}
            >
              <Trash2 size={12} /> Clear all
            </button>
          </div>
        </div>
      )}

      {/* ── Bulk actions bar ─────────────────────────────────────────── */}
      {selectMode && selectedIds.size > 0 && (
        <div
          className="flex items-center gap-3 flex-wrap rounded-xl"
          style={{ padding: '10px 16px', background: 'var(--accent-dim)', border: '1px solid var(--accent)' }}
        >
          <span className="text-sm font-semibold tnum" style={{ color: 'var(--accent-text)' }}>
            {selectedIds.size} selected
          </span>
          <button
            onClick={() => selectedIds.size === filtered.length ? deselectAll() : selectAll()}
            className="relative text-xs font-semibold before:absolute before:-inset-y-2.5 before:inset-x-0 before:content-['']"
            style={{ color: 'var(--accent-text)', background: 'none', border: 'none', cursor: 'pointer' }}
          >
            {selectedIds.size === filtered.length ? 'Deselect all' : 'Select all'}
          </button>
          <div className="flex-1" />
          <div className="flex gap-1.5 flex-wrap">
            {(Object.entries(STATUS_META) as [ContactStatus, typeof STATUS_META[ContactStatus]][]).map(([key, meta]) => (
              <button
                key={key}
                onClick={() => bulkSetStatus(key)}
                disabled={bulkBusy}
                className="relative text-[11px] px-2 py-0.5 rounded-full font-bold font-mono transition-all before:absolute before:-inset-y-2.5 before:inset-x-0 before:content-['']"
                style={{
                  background: meta.bg,
                  color: meta.color,
                  border: `1px solid color-mix(in srgb, ${meta.color} 31%, transparent)`,
                  cursor: 'pointer',
                }}
              >
                {meta.label}
              </button>
            ))}
          </div>
          <button
            onClick={bulkDelete}
            disabled={bulkBusy}
            className="btn text-xs flex items-center gap-1"
            style={{
              color: 'var(--danger-text)',
              borderColor: 'color-mix(in srgb, var(--danger) 30%, transparent)',
              background: 'color-mix(in srgb, var(--danger) 8%, transparent)',
            }}
          >
            <Trash2 size={11} /> Delete
          </button>
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
              : emptyHuntMessage(huntInfo.query, huntInfo.found, huntInfo.duplicates, huntInfo.roleFiltered)}
          </span>
          {contacts.length > 0 && (
            <button
              onClick={clearHunt}
              className="font-semibold flex-shrink-0"
              style={{ color: 'var(--accent-text)', background: 'none', border: 'none', cursor: 'pointer', fontSize: 12 }}
            >
              Show all <span className="tnum">{contacts.length}</span> contacts →
            </button>
          )}
        </p>
      )}

      {/* ── Contact grid ─────────────────────────────────────────────── */}
      {!hunting && (filtered.length > 0 ? (
        <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
          {filtered.map(c => (
            <ContactCard
              key={c.id}
              contact={c}
              selectable={selectMode}
              selected={selectedIds.has(c.id)}
              onToggleSelect={() => toggleSelect(c.id)}
              onOpenDetails={() => setDrawerId(c.id)}
            />
          ))}
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

      {/* ── Contact detail drawer ────────────────────────────────────── */}
      <ContactDrawer contact={drawerContact} onClose={() => setDrawerId(null)} />
    </div>
  )
}
