import { useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { X, Check } from 'lucide-react'
import { contactsApi } from '../../api/contacts'
import { useStore } from '../../store'
import { STATUS_META, type Contact, type ContactStatus } from '../../types'
import { contactDisplayName, isGenericName } from '../../lib/display'

interface Props {
  contact: Contact
  selectable?: boolean
  selected?: boolean
  onToggleSelect?: () => void
}

export default function ContactCard({ contact: c, selectable, selected, onToggleSelect }: Props) {
  const { upsertContact, removeContact, removeHuntResult, updateHuntResult } = useStore()
  const qc = useQueryClient()

  // Cards can render from the hunt-results list (fresh hunt) OR the saved
  // contacts list — both must be updated or the change looks like it failed.
  const statusMutation = useMutation({
    mutationFn: (status: ContactStatus) => contactsApi.setStatus(c.id, status),
    onSuccess: (updated) => {
      upsertContact(updated)
      updateHuntResult(updated)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const deleteMutation = useMutation({
    mutationFn: () => contactsApi.delete(c.id),
    onSuccess: () => {
      removeContact(c.id)
      removeHuntResult(c.id)
      qc.invalidateQueries({ queryKey: ['contacts'] })
      toast('Contact removed', { icon: '🗑️' })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  // Color only — tier labels (P1/P2/…) were internal ranking jargon that
  // confused more than informed; the designation text says who this is.
  const getDesigColor = (d: string) => {
    const dl = d.toLowerCase()
    if (['founder', 'co-founder', 'ceo', 'cto', 'chief', 'founding'].some(x => dl.includes(x)))
      return '#6f5ae0'
    if (['hr', 'human resource', 'talent', 'recruiter', 'recruiting', 'people ops', 'people partner'].some(x => dl.includes(x)))
      return '#c47d1e'
    if (['engineer', 'developer', 'swe', 'software', 'backend', 'frontend', 'fullstack', 'devops', 'data'].some(x => dl.includes(x)))
      return '#0e9d88'
    return '#8a7f70'
  }

  const displayName = contactDisplayName(c)
  const generic = isGenericName(c.name)
  const initials = displayName.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase()
  const desigColor = getDesigColor(c.designation)

  return (
    <div
      className="card relative group"
      style={{
        transition: 'border-color .15s',
        ...(selected ? { borderColor: 'var(--accent)', boxShadow: 'var(--glow-accent)' } : {}),
      }}
      onClick={selectable ? onToggleSelect : undefined}
    >
      {/* ── Top-right controls ── */}
      <div className="absolute top-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <button
          onClick={e => { e.stopPropagation(); deleteMutation.mutate() }}
          title="Remove"
          className="w-5 h-5 flex items-center justify-center rounded"
          style={{ background: 'rgba(210,72,58,.08)', color: '#8a7f70' }}
        >
          <X size={10} />
        </button>
      </div>

      {/* ── Checkbox (bulk select mode) ── */}
      {selectable && (
        <div className="absolute top-2 left-2">
          <div
            className="w-5 h-5 rounded flex items-center justify-center transition-colors"
            style={{
              border: `2px solid ${selected ? 'var(--accent)' : 'var(--border-strong)'}`,
              background: selected ? 'var(--accent)' : 'transparent',
            }}
          >
            {selected && <Check size={12} color="#fff" strokeWidth={3} />}
          </div>
        </div>
      )}

      {/* ── Avatar + name ── */}
      <div className="flex items-center gap-3 mb-3 pr-6" style={selectable ? { paddingLeft: 24 } : undefined}>
        <div
          className="w-9 h-9 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0"
          style={{ background: `${desigColor}18`, color: desigColor, border: `1.5px solid ${desigColor}30` }}
        >
          {initials}
        </div>
        <div className="min-w-0">
          <div className="text-sm font-medium truncate">{displayName}</div>
          <span
            className="badge"
            style={{ background: `${desigColor}18`, color: desigColor, fontSize: '9px', marginTop: '2px' }}
          >
            {c.designation}
          </span>
        </div>
      </div>

      {/* ── Company + email ── */}
      <div className="text-xs mb-1" style={{ color: generic ? 'var(--text)' : 'var(--text-muted)', fontWeight: generic ? 600 : 400 }}>🏢 {c.company}</div>
      <div className="flex items-center gap-1.5 mb-3">
        <span className="text-xs font-mono truncate" style={{ color: generic ? 'var(--text-muted)' : 'var(--text-dim)' }}>{c.email}</span>
      </div>
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
