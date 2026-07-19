import { useEffect, useRef, type ReactNode } from 'react'

/**
 * Minimal confirmation modal. Closes on backdrop click or Escape.
 * Keeps destructive / irreversible actions behind an explicit second click.
 *
 * A11y: focuses Cancel on open (the safe default for a destructive prompt),
 * traps Tab inside the dialog, and returns focus to the opener on close.
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
  const dialogRef = useRef<HTMLDivElement>(null)
  const cancelRef = useRef<HTMLButtonElement>(null)

  // Remember what was focused before the dialog opened, focus Cancel on
  // mount, and hand focus back when the dialog unmounts.
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    cancelRef.current?.focus()
    return () => opener?.focus()
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { onCancel(); return }
      if (e.key !== 'Tab') return
      // Trap Tab: cycle between the dialog's focusable elements.
      const root = dialogRef.current
      if (!root) return
      const focusables = Array.from(
        root.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        )
      ).filter(el => !el.hasAttribute('disabled'))
      if (focusables.length === 0) { e.preventDefault(); return }
      const first = focusables[0]
      const last = focusables[focusables.length - 1]
      const active = document.activeElement
      const inside = active instanceof HTMLElement && root.contains(active)
      if (e.shiftKey) {
        if (!inside || active === first) { e.preventDefault(); last.focus() }
      } else {
        if (!inside || active === last) { e.preventDefault(); first.focus() }
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onCancel])

  // Button labels are sub-18px text, so the -text variants carry the color;
  // the base hue only ever appears diluted through color-mix fills/borders.
  const accent = danger ? 'var(--danger-text)' : 'var(--accent-text)'
  const accentBg = danger
    ? 'color-mix(in srgb, var(--danger) 12%, transparent)'
    : 'color-mix(in srgb, var(--accent) 15%, transparent)'
  const accentBorder = danger
    ? 'color-mix(in srgb, var(--danger) 40%, transparent)'
    : 'color-mix(in srgb, var(--accent) 40%, transparent)'

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
        ref={dialogRef}
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
            ref={cancelRef}
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
