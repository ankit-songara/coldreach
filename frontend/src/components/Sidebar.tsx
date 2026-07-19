import { Search, LogOut } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useState } from 'react'
import Logo from './shared/Logo'
import { getStoredTheme, cycleTheme, type Theme } from '../lib/theme'
import { Sun, Moon, Monitor } from 'lucide-react'

const THEME_ICON: Record<Theme, LucideIcon> = { light: Sun, dark: Moon, system: Monitor }
const THEME_LABEL: Record<Theme, string> = { light: 'Light', dark: 'Dark', system: 'System' }

export interface SidebarItem {
  id:     string
  icon:   LucideIcon
  label:  string
  badge?: number
}

// v2 desktop shell: persistent left rail with nav, the ⌘K entry point, the
// daily-send meter, theme, and the user row. Mobile keeps the bottom tab bar.
export default function Sidebar({
  items, activeId, onSelect, onOpenPalette, sentToday, sendCap, email, onLogout,
}: {
  items: SidebarItem[]
  activeId: string
  onSelect: (id: string) => void
  onOpenPalette: () => void
  sentToday: number
  sendCap: number
  email: string
  onLogout: () => void
}) {
  const [theme, setTheme] = useState<Theme>(getStoredTheme)
  const ThemeIcon = THEME_ICON[theme]
  const initials = (email.split('@')[0] || '?').slice(0, 2).toUpperCase()
  const pct = Math.min(100, Math.round((sentToday / Math.max(1, sendCap)) * 100))

  return (
    <aside
      className="hidden md:flex flex-col fixed left-0 top-0 bottom-0 z-40"
      aria-label="Primary"
      style={{
        width: 232, padding: '20px 14px 16px',
        background: 'var(--surface-1)', borderRight: '1px solid var(--border)',
      }}
    >
      {/* Brand */}
      <div style={{ padding: '0 8px', marginBottom: 18 }}>
        <Logo size={28} wordmark />
      </div>

      {/* ⌘K entry */}
      <button
        onClick={onOpenPalette}
        className="flex items-center gap-2 text-[13px] font-medium"
        style={{
          padding: '8px 12px', borderRadius: 10, cursor: 'pointer', marginBottom: 14,
          background: 'var(--surface-2)', border: '1px solid var(--border)',
          color: 'var(--text-muted)',
        }}
      >
        <Search size={13} />
        <span className="flex-1 text-left">Jump or act…</span>
        <kbd
          className="text-[10px] font-mono font-semibold"
          style={{
            padding: '2px 5px', borderRadius: 5, background: 'var(--surface-1)',
            border: '1px solid var(--border)', color: 'var(--text-dim)',
          }}
        >
          ⌘K
        </kbd>
      </button>

      {/* Nav */}
      <nav className="flex flex-col gap-0.5" aria-label="Sections">
        {items.map(item => {
          const Icon = item.icon
          const active = item.id === activeId
          return (
            <button
              key={item.id}
              onClick={() => onSelect(item.id)}
              aria-current={active ? 'page' : undefined}
              className="flex items-center gap-2.5 text-[13.5px] font-semibold transition-colors"
              style={{
                padding: '9px 12px', borderRadius: 10, border: 'none', cursor: 'pointer',
                textAlign: 'left',
                background: active ? 'var(--accent-tint)' : 'transparent',
                color: active ? 'var(--accent-text)' : 'var(--text-muted)',
              }}
            >
              <Icon size={16} strokeWidth={active ? 2.4 : 2} />
              <span className="flex-1">{item.label}</span>
              {item.badge != null && item.badge > 0 && (
                <span
                  className="tnum text-[11px] font-bold"
                  style={{
                    padding: '1px 7px', borderRadius: 'var(--radius-full)',
                    background: active ? 'var(--surface-1)' : 'var(--surface-2)',
                    color: active ? 'var(--accent-text)' : 'var(--text-muted)',
                  }}
                >
                  {item.badge > 99 ? '99+' : item.badge}
                </span>
              )}
            </button>
          )
        })}
      </nav>

      <div className="flex-1" />

      {/* Daily send meter */}
      <div
        style={{
          padding: '11px 12px', borderRadius: 12, marginBottom: 10,
          background: 'var(--surface-2)', border: '1px solid var(--border)',
        }}
      >
        <div className="flex items-baseline justify-between mb-1.5">
          <span
            className="text-[10px] font-mono font-bold tracking-widest"
            style={{ color: 'var(--text-muted)' }}
          >
            TODAY'S SENDS
          </span>
          <span className="tnum text-[12px] font-bold" style={{ color: 'var(--text)' }}>
            {sentToday}<span style={{ color: 'var(--text-muted)', fontWeight: 500 }}>/{sendCap}</span>
          </span>
        </div>
        <div style={{ height: 6, borderRadius: 99, background: 'var(--surface-3)', overflow: 'hidden' }}>
          <div style={{
            height: '100%', width: `${pct}%`, borderRadius: 99,
            background: pct >= 100 ? 'var(--warning)' : 'var(--accent)',
            transition: 'width .4s var(--ease-out)',
          }} />
        </div>
      </div>

      {/* Theme */}
      <button
        onClick={() => setTheme(cycleTheme())}
        className="flex items-center gap-2.5 text-[13px] font-medium"
        style={{
          padding: '8px 12px', borderRadius: 10, border: 'none', cursor: 'pointer',
          background: 'transparent', color: 'var(--text-muted)', textAlign: 'left',
        }}
      >
        <ThemeIcon size={15} />
        {THEME_LABEL[theme]} theme
      </button>

      {/* User row */}
      <div
        className="flex items-center gap-2.5"
        style={{ padding: '10px 12px 2px', borderTop: '1px solid var(--border)', marginTop: 8 }}
      >
        <span
          className="w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-bold flex-shrink-0"
          style={{ background: 'var(--accent-tint)', color: 'var(--accent-text)' }}
        >
          {initials}
        </span>
        <span className="text-[12px] font-medium truncate flex-1" style={{ color: 'var(--text-muted)' }}>
          {email}
        </span>
        <button
          onClick={onLogout}
          aria-label="Sign out"
          title="Sign out"
          className="hit-target"
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', display: 'flex' }}
        >
          <LogOut size={14} />
        </button>
      </div>
    </aside>
  )
}
