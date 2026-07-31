import { useEffect, useMemo, useRef, useState } from 'react'
import type { LucideIcon } from 'lucide-react'

export interface Command {
  id:    string
  icon:  LucideIcon
  label: string
  kind:  string        // small right-aligned tag: "view" | "action" | …
  run:   () => void
}

// ⌘K palette (v2 concept): jump anywhere or run an action from the keyboard.
// The parent owns the open state and the command list; this stays dumb.
export default function CommandPalette({ open, onClose, commands }: {
  open: boolean
  onClose: () => void
  commands: Command[]
}) {
  const [query, setQuery] = useState('')
  const [cursor, setCursor] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const activeRef = useRef<HTMLButtonElement>(null)
  const openerRef = useRef<HTMLElement | null>(null)

  const hits = useMemo(() => {
    const q = query.trim().toLowerCase()
    return q ? commands.filter(c => c.label.toLowerCase().includes(q)) : commands
  }, [query, commands])

  // Reset + focus on every open; return focus to the opener on close.
  useEffect(() => {
    if (open) {
      openerRef.current = document.activeElement as HTMLElement | null
      setQuery('')
      setCursor(0)
      // after the overlay paints
      requestAnimationFrame(() => inputRef.current?.focus())
    } else {
      openerRef.current?.focus?.()
    }
  }, [open])

  useEffect(() => { setCursor(0) }, [query])

  // Keyboard-scrolled cursor must stay visible in the scrollable list.
  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: 'nearest' })
  }, [cursor])

  // Document-level Escape + Tab trap (ConfirmDialog's pattern) and body
  // scroll lock. Escape previously only worked while the search input had
  // focus; a Tab (or a click into the list) escaped the dialog entirely.
  // stopPropagation so a drawer open UNDERNEATH doesn't also close.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onClose(); return }
      if (e.key !== 'Tab') return
      const root = panelRef.current
      if (!root) return
      const focusables = Array.from(
        root.querySelectorAll<HTMLElement>('button, input, [tabindex]:not([tabindex="-1"])')
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
    // Capture phase so this dialog (highest layer) wins over the drawer's
    // bubble-phase listener.
    document.addEventListener('keydown', onKey, true)
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey, true)
      document.body.style.overflow = prevOverflow
    }
  }, [open, onClose])

  if (!open) return null

  const runAt = (i: number) => {
    const cmd = hits[i]
    if (!cmd) return
    onClose()
    cmd.run()
  }

  return (
    <div
      role="dialog" aria-modal="true" aria-label="Command palette"
      className="fixed inset-0 z-[70] flex items-start justify-center px-4"
      style={{ background: 'rgba(0,0,0,0.45)', paddingTop: 'clamp(60px, 16vh, 160px)' }}
      onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        ref={panelRef}
        className="w-full overflow-hidden"
        style={{
          maxWidth: 520, background: 'var(--surface-1)', borderRadius: 16,
          border: '1px solid var(--border-strong)', boxShadow: 'var(--shadow-lg)',
          animation: 'cr-pop .18s var(--ease-spring) both',
        }}
      >
        <input
          ref={inputRef}
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Escape') { e.preventDefault(); onClose() }
            else if (e.key === 'ArrowDown') { e.preventDefault(); setCursor(c => Math.min(c + 1, hits.length - 1)) }
            else if (e.key === 'ArrowUp')   { e.preventDefault(); setCursor(c => Math.max(c - 1, 0)) }
            else if (e.key === 'Enter')     { e.preventDefault(); runAt(cursor) }
          }}
          placeholder="Jump or act…"
          aria-label="Search commands"
          className="w-full text-[15px] outline-none"
          style={{
            padding: '15px 18px', background: 'transparent', border: 'none',
            borderBottom: '1px solid var(--border)', color: 'var(--text)',
          }}
        />
        <div role="listbox" aria-label="Commands" className="max-h-[320px] overflow-y-auto py-1.5">
          {hits.length === 0 && (
            <p className="text-sm text-center py-6" style={{ color: 'var(--text-muted)' }}>
              Nothing matches “{query.trim()}”
            </p>
          )}
          {hits.map((cmd, i) => {
            const Icon = cmd.icon
            const active = i === cursor
            return (
              <button
                key={cmd.id}
                ref={active ? activeRef : undefined}
                role="option" aria-selected={active}
                onMouseEnter={() => setCursor(i)}
                onClick={() => runAt(i)}
                className="flex items-center gap-3 w-full text-sm font-medium"
                style={{
                  padding: '10px 18px', border: 'none', cursor: 'pointer', textAlign: 'left',
                  background: active ? 'var(--surface-2)' : 'transparent',
                  color: 'var(--text)',
                }}
              >
                <Icon size={15} style={{ color: active ? 'var(--accent)' : 'var(--text-muted)', flexShrink: 0 }} />
                <span className="flex-1 truncate">{cmd.label}</span>
                <span
                  className="text-[10px] font-mono font-semibold uppercase tracking-wider"
                  style={{ color: 'var(--text-dim)' }}
                >
                  {cmd.kind}
                </span>
              </button>
            )
          })}
        </div>
        <div
          className="flex items-center gap-4 text-[11px] font-mono"
          style={{ padding: '9px 18px', borderTop: '1px solid var(--border)', color: 'var(--text-muted)' }}
        >
          <span>↵ run</span><span>esc close</span><span className="ml-auto">⌘K toggle</span>
        </div>
      </div>
    </div>
  )
}
