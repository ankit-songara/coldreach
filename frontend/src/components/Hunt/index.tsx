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

const STATUS_FILTERS: Array<ContactStatus | 'all'> = ['all', 'new', 'emailed', 'followed_up', 'replied', 'interview']

export default function Hunt() {
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<ContactStatus | 'all'>('all')
  const [verifying, setVerifying] = useState(false)
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

  const huntMutation = useMutation({
    mutationFn: huntApi.hunt,
    onSuccess: (data) => {
      toast.success(`Found ${data.total} contacts — ${Object.entries(data.sources).map(([s,n]) => `${s}: ${n}`).join(', ')}`)
      qc.invalidateQueries({ queryKey: ['contacts'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const exportCSV = () => {
    const header = 'Name,Email,Designation,Company,Status,Source'
    const rows = contacts.map(c =>
      [c.name, c.email, c.designation, c.company, c.status, c.source]
        .map(v => `"${(v ?? '').replace(/"/g, '""')}"`)
        .join(',')
    )
    const blob = new Blob([[header, ...rows].join('\n')], { type: 'text/csv' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href = url
    a.download = `coldreach-${new Date().toISOString().slice(0,10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const filtered = statusFilter === 'all'
    ? contacts
    : contacts.filter(c => c.status === statusFilter)

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-wide mb-1" style={{ fontFamily: 'Rajdhani' }}>
          Hunt Contacts
        </h1>
        <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
          Scrapes HackerNews "Who is Hiring", GitHub commit emails, Wellfound & company sites — no Claude.
        </p>
      </div>

      {/* ── Search bar ──────────────────────────────────────────────── */}
      <div className="flex gap-2">
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && query.trim() && huntMutation.mutate({ query })}
          placeholder="golang, react engineer, python backend…"
          className="input flex-1"
        />
        <button
          onClick={() => huntMutation.mutate({ query })}
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
            onClick={() => setQuery(chip)}
            className="text-xs px-3 py-1.5 rounded-full border font-mono transition-colors hover:border-accent"
            style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
          >
            {chip}
          </button>
        ))}
      </div>

      {/* ── Toolbar: filters + actions ───────────────────────────────── */}
      {contacts.length > 0 && (
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex gap-2 flex-wrap">
            {STATUS_FILTERS.map(s => {
              const count = s === 'all' ? contacts.length : contacts.filter(c => c.status === s).length
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
              title="Check which emails are deliverable (MX + syntax)"
              className="btn btn-ghost flex items-center gap-1 text-xs"
              style={{ color: '#34d399', borderColor: 'rgba(52,211,153,0.3)' }}
            >
              <ShieldCheck size={12} /> {verifying ? 'Verifying…' : 'Verify'}
            </button>
            <button onClick={exportCSV} className="btn btn-ghost flex items-center gap-1 text-xs">
              <Download size={12} /> CSV
            </button>
            <button
              onClick={() => { clearContacts(); qc.invalidateQueries({ queryKey: ['contacts'] }) }}
              className="btn btn-ghost flex items-center gap-1 text-xs"
              style={{ color: '#ef4444' }}
            >
              <Trash2 size={12} /> Clear all
            </button>
          </div>
        </div>
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
