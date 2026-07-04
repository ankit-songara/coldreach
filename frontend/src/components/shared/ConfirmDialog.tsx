import { useEffect, type ReactNode } from 'react'

/**
 * Minimal confirmation modal. Closes on backdrop click or Escape.
 * Keeps destructive / irreversible actions behind an explicit second click.
 */
export default function ConfirmDialog({
  title, children, confirmLabel, danger = false, busy = false, onConfirm, onCancel,
}: {
  title: string
  children: ReactNode
  confirmLabel: string
  danger?: boolean
  busy?: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onCancel])

  const accent = danger ? '#d2483a' : 'var(--accent)'
  const accentBg = danger ? 'rgba(210,72,58,0.12)' : 'rgba(226,96,63,0.15)'
  const accentBorder = danger ? 'rgba(210,72,58,0.4)' : 'rgba(226,96,63,0.4)'

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.6)' }}
      onClick={onCancel}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div
        className="card w-full max-w-sm mx-4 space-y-4"
        style={{ border: `1px solid ${accentBorder}` }}
        onClick={e => e.stopPropagation()}
      >
        <h3 className="font-bold text-base">{title}</h3>
        <div className="text-sm space-y-2" style={{ color: 'var(--text-muted)' }}>
          {children}
        </div>
        <div className="flex gap-2 pt-1">
          <button
            onClick={onConfirm}
            disabled={busy}
            className="btn flex-1 flex items-center justify-center gap-2 font-semibold"
            style={{ background: accentBg, borderColor: accentBorder, color: accent, opacity: busy ? 0.6 : 1 }}
          >
            {busy ? 'Working…' : confirmLabel}
          </button>
          <button
            onClick={onCancel}
            disabled={busy}
            className="btn"
            style={{ color: 'var(--text-muted)', borderColor: 'var(--border)' }}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}
