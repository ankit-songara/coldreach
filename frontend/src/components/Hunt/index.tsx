import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Search, Trash2, Download, ShieldCheck } from 'lucide-react'
import { useStore } from '../../store'
import { huntApi } from '../../api/hunt'
import { contactsApi } from '../../api/contacts'
import { verifyApi } from '../../api/verify'
import ContactCard from './ContactCard'
import type { ContactStatus } from '../../types'

const CHIPS = [
  'golang hiring',
  'react engineer hiring',
  'python backend hiring',
  'founding engineer india',
  'devops kubernetes hiring',
  'data engineer india',
]

const STATUS_FILTERS: Array<ContactStatus | 'all'> = ['all', 'new', 'emailed', 'followed_up', 'replied', 'interview', 'offer']

// Honest, specific empty-state copy based on what the hunt actually found.
function emptyHuntMessage(query: string, found: number, duplicates: number): string {
  if (duplicates > 0)
    return `Every match for "${query}" is already in your list (${duplicates} contact${duplicates > 1 ? 's' : ''}). Try a different query.`
  if (found > 0)
    return `Found ${found} lead${found > 1 ? 's' : ''} hiring for "${query}", but couldn't resolve a direct email — large companies route everything through portals (no free tool reaches them). Try a startup name or a role like "react engineer remote", or set a free GITHUB_TOKEN to sharpen email detection.`
  return `No matches for "${query}". Try a role query like "react engineer remote", or a specific startup name (e.g. "Linear", "Supabase").`
}

export default function Hunt() {
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<ContactStatus | 'all'>('all')
  const [verifying, setVerifying] = useState(false)
  const [huntResults, setHuntResults] = useState<any[] | null>(null)
  const [huntInfo, setHuntInfo] = useState<{ found: number; duplicates: number; query: string } | null>(null)
  const { setContacts, contacts, clearContacts, upsertContact } = useStore()
  const qc = useQueryClient()

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
      toast.error(e.response?.data?.detail ?? e.message)
    } finally {
      setVerifying(false)
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
    if (!q.trim()) return
    setHuntResults(null)   // clear previous results while hunting
    setHuntInfo(null)
    huntMutation.mutate({ query: q })
  }

  const huntMutation = useMutation({
    mutationFn: huntApi.hunt,
    onSuccess: (data, variables) => {
      setHuntResults(data.contacts)
      setHuntInfo({ found: data.found ?? 0, duplicates: data.duplicates ?? 0, query: variables.query })
      qc.invalidateQueries({ queryKey: ['contacts'] })
      if (data.total > 0) {
        const sources = Object.entries(data.sources)
          .filter(([, n]) => (n as number) > 0)
          .map(([s, n]) => `${s}: ${n}`).join(', ')
        toast.success(`Found ${data.total} new contacts — ${sources}`)
      } else if ((data.duplicates ?? 0) > 0) {
        toast('Already in your list', { icon: '✅' })
      } else if ((data.found ?? 0) > 0) {
        toast('Found roles, but no direct email — see note below', { icon: '📭' })
      } else {
        toast(`No matches for "${variables.query}"`, { icon: '🔍' })
      }
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const exportCSV = () => {
    const header = 'Name,Email,Designation,Company,Status,Source'
    // Guard against CSV formula injection: a field starting with = + - @ can be
    // executed as a formula when opened in Excel/Sheets — prefix it with a quote.
    const cell = (v: string) => {
      let s = v ?? ''
      if (/^[=+\-@]/.test(s)) s = "'" + s
      return `"${s.replace(/"/g, '""')}"`
    }
    const rows = contacts.map(c =>
      [c.name, c.email, c.designation, c.company, c.status, c.source].map(cell).join(',')
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
          Scrapes 17 free sources — HackerNews (Who-is-Hiring + YC job posts), GitHub,
          165+ ATS boards (Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee, Workable,
          Breezy) and remote boards (RemoteOK, Remotive, Jobicy, Himalayas, The Muse,
          WeWorkRemotely). No API keys.
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
        />
        <button
          onClick={() => doHunt(query)}
          disabled={!query.trim() || huntMutation.isPending}
          className="btn btn-primary flex items-center gap-2"
        >
          <Search size={14} />
          {huntMutation.isPending ? 'Hunting…' : 'Hunt'}
        </button>
      </div>

      {/* ── Query chips ─────────────────────────────────────────────── */}
      <div className="flex flex-wrap gap-2">
        {CHIPS.map(chip => (
          <button
            key={chip}
            onClick={() => { setQuery(chip); doHunt(chip) }}
            className="text-xs px-3 py-1.5 rounded-full border font-mono transition-colors hover:border-accent"
            style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
          >
            {chip}
          </button>
        ))}
      </div>

      {/* ── Toolbar: filters + actions ───────────────────────────────── */}
      {displayList.length > 0 && (
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex gap-2 flex-wrap">
            {STATUS_FILTERS.map(s => {
              const count = s === 'all' ? displayList.length : displayList.filter((c: any) => c.status === s).length
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
                  {s} ({count})
                </button>
              )
            })}
          </div>
          <div className="flex gap-2">
            <button
              onClick={handleVerify}
              disabled={verifying}
              title="Re-check deliverability (MX + syntax, or Hunter.io if a key is configured)"
              className="btn btn-ghost flex items-center gap-1 text-xs"
              style={{ color: '#3f8f43', borderColor: 'rgba(63,143,67,0.3)' }}
            >
              <ShieldCheck size={12} /> {verifying ? 'Verifying…' : 'Verify'}
            </button>
            <button onClick={exportCSV} className="btn btn-ghost flex items-center gap-1 text-xs">
              <Download size={12} /> CSV
            </button>
            <button
              onClick={() => { clearContacts(); qc.invalidateQueries({ queryKey: ['contacts'] }) }}
              className="btn btn-ghost flex items-center gap-1 text-xs"
              style={{ color: '#d2483a' }}
            >
              <Trash2 size={12} /> Clear all
            </button>
          </div>
        </div>
      )}

      {/* ── Result context label ─────────────────────────────────────── */}
      {huntResults !== null && huntInfo && (
        <p className="text-xs" style={{ color: 'var(--text-muted)', lineHeight: 1.6 }}>
          {huntResults.length > 0
            ? `Showing ${huntResults.length} new contact${huntResults.length !== 1 ? 's' : ''} found for "${huntInfo.query}"`
            : emptyHuntMessage(huntInfo.query, huntInfo.found, huntInfo.duplicates)}
        </p>
      )}

      {/* ── Contact grid ─────────────────────────────────────────────── */}
      {filtered.length > 0 ? (
        <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
          {filtered.map(c => <ContactCard key={c.id} contact={c} />)}
        </div>
      ) : (
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
      )}
    </div>
  )
}
