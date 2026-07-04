// Deliverability badge shown next to a contact's email — shared by Compose & Send.
export default function EmailBadge({ status, confidence }: { status?: string; confidence?: number }) {
  if (!status || status === 'unknown') return null
  const meta: Record<string, { label: string; color: string; bg: string; sym: string }> = {
    valid:   { label: 'verified', sym: '✓', color: '#3f8f43', bg: 'rgba(63,143,67,.12)'  },
    risky:   { label: 'risky',    sym: '~', color: '#c47d1e', bg: 'rgba(196,125,30,.12)' },
    invalid: { label: 'invalid',  sym: '✗', color: '#d2483a', bg: 'rgba(210,72,58,.12)'  },
  }
  const m = meta[status] ?? { label: status, sym: '?', color: '#8a7f70', bg: 'rgba(138,127,112,.12)' }
  return (
    <span
      className="badge inline-flex items-center gap-0.5"
      title={`Email deliverability: ${m.label}${confidence != null ? ` (${confidence}% confidence)` : ''}`}
      style={{ background: m.bg, color: m.color, fontSize: '9px', whiteSpace: 'nowrap' }}
    >
      {m.sym} {m.label}{confidence != null ? ` · ${confidence}%` : ''}
    </span>
  )
}
