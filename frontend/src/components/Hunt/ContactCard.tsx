import { useMutation, useQueryClient } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { contactsApi } from '../../api/contacts'
import { useStore } from '../../store'
import { STATUS_META, type Contact, type ContactStatus } from '../../types'

interface Props { contact: Contact }

export default function ContactCard({ contact: c }: Props) {
  const { upsertContact, removeContact } = useStore()
  const qc = useQueryClient()

  const statusMutation = useMutation({
    mutationFn: (status: ContactStatus) => contactsApi.setStatus(c.id, status),
    onSuccess: (updated) => upsertContact(updated),
    onError: () => {},
  })

  const deleteMutation = useMutation({
    mutationFn: () => contactsApi.delete(c.id),
    onSuccess: () => {
      removeContact(c.id)
      qc.invalidateQueries({ queryKey: ['contacts'] })
    },
  })

  const getDesigTier = (d: string) => {
    const dl = d.toLowerCase()
    if (['founder', 'co-founder', 'ceo', 'cto', 'chief', 'founding'].some(x => dl.includes(x)))
      return { color: '#a78bfa', label: 'P1 · Founder/CxO' }
    if (['hr', 'human resource', 'talent', 'recruiter', 'recruiting', 'people ops', 'people partner'].some(x => dl.includes(x)))
      return { color: '#f59e0b', label: 'P2 · HR/TA' }
    if (['engineer', 'developer', 'swe', 'software', 'backend', 'frontend', 'fullstack', 'devops', 'data'].some(x => dl.includes(x)))
      return { color: '#22d3ee', label: 'P3 · Engineer' }
    return { color: '#64748b', label: '' }
  }

  const initials = c.name.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase()
  const tier = getDesigTier(c.designation)
  const desigColor = tier.color

  return (
    <div className="card relative group" style={{ transition: 'border-color .15s' }}>
      {/* ── Top-right controls ── */}
      <div className="absolute top-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <button
          onClick={() => deleteMutation.mutate()}
          title="Remove"
          className="w-5 h-5 flex items-center justify-center rounded"
          style={{ background: 'rgba(239,68,68,.08)', color: '#64748b' }}
        >
          <X size={10} />
        </button>
      </div>

      {/* ── Avatar + name ── */}
      <div className="flex items-center gap-3 mb-3 pr-6">
        <div
          className="w-9 h-9 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0"
          style={{ background: `${desigColor}18`, color: desigColor, border: `1.5px solid ${desigColor}30` }}
        >
          {initials}
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <div className="text-sm font-medium truncate">{c.name}</div>
            {tier.label && (
              <span
                className="badge flex-shrink-0"
                style={{ background: `${desigColor}22`, color: desigColor, fontSize: '8px', fontWeight: 700, letterSpacing: '0.03em' }}
              >
                {tier.label}
              </span>
            )}
          </div>
          <span
            className="badge"
            style={{ background: `${desigColor}18`, color: desigColor, fontSize: '9px', marginTop: '2px' }}
          >
            {c.designation}
          </span>
        </div>
      </div>

      {/* ── Company + email ── */}
      <div className="text-xs mb-1" style={{ color: 'var(--text-muted)' }}>🏢 {c.company}</div>
      <div className="flex items-center gap-1.5 mb-3">
        <span className="text-xs font-mono truncate" style={{ color: 'var(--text-dim)' }}>{c.email}</span>
        {c.email_status && c.email_status !== 'unknown' && (
          <span
            className="badge flex-shrink-0"
            style={{
              fontSize: '8px', fontWeight: 700,
              ...(c.email_status === 'valid'
                ? { background: 'rgba(52,211,153,0.14)', color: '#34d399' }
                : c.email_status === 'risky'
                ? { background: 'rgba(245,158,11,0.14)', color: '#f59e0b' }
                : { background: 'rgba(239,68,68,0.14)', color: '#ef4444' }),
            }}
            title={`Email verification: ${c.email_status}`}
          >
            {c.email_status === 'valid' ? '✓' : c.email_status === 'risky' ? '~' : '✕'} {c.email_status}
          </span>
        )}
      </div>
      {c.source && (
        <div className="text-xs mb-3" style={{ color: 'var(--text-dim)' }}>via {c.source}</div>
      )}

      {/* ── Status pills ── */}
      <div className="flex flex-wrap gap-1">
        {(Object.entries(STATUS_META) as [ContactStatus, typeof STATUS_META[ContactStatus]][]).map(([key, meta]) => (
          <button
            key={key}
            onClick={() => statusMutation.mutate(key)}
            disabled={statusMutation.isPending}
            className="text-xs px-2 py-0.5 rounded-full font-bold font-mono transition-all"
            style={{
              background: c.status === key ? meta.bg    : 'transparent',
              color:      c.status === key ? meta.color : 'var(--text-dim)',
              border:     `1px solid ${c.status === key ? meta.color + '50' : 'var(--border)'}`,
            }}
          >
            {meta.label}
          </button>
        ))}
      </div>
    </div>
  )
}
