import { useEffect, useState, useRef, createContext, type ReactNode } from 'react'
import { LogOut, Send as SendIcon, ChevronDown, Home, Settings, Search, Wand2, Sun, Moon, Monitor } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useStore } from './store'
import { resumeApi } from './api/resume'
import { authApi } from './api/auth'
import { contactsApi } from './api/contacts'
import { getStoredTheme, cycleTheme, type Theme } from './lib/theme'
import Auth    from './components/Auth'
import Setup   from './components/Setup'
import Hunt    from './components/Hunt'
import Compose from './components/Compose'
import Send    from './components/Send'
import Today   from './components/Today'

type TabId = 'today' | 'setup' | 'hunt' | 'compose' | 'send'

export const ResumeReadyCtx = createContext(false)

const TABS: Array<{ id: TabId; num: string | null; label: string }> = [
  { id: 'today',   num: null, label: 'Today'   },
  { id: 'setup',   num: '01', label: 'Setup'   },
  { id: 'hunt',    num: '02', label: 'Hunt'    },
  { id: 'compose', num: '03', label: 'Compose' },
  { id: 'send',    num: '04', label: 'Send'    },
]

const TAB_IDS = TABS.map(t => t.id)
const isTabId = (v: string): v is TabId => (TAB_IDS as string[]).includes(v)

// Icons for the mobile bottom tab bar
const TAB_ICONS: Record<TabId, LucideIcon> = {
  today: Home, setup: Settings, hunt: Search, compose: Wand2, send: SendIcon,
}

const THEME_ICON: Record<Theme, typeof Sun> = { light: Sun, dark: Moon, system: Monitor }
const THEME_LABEL: Record<Theme, string> = { light: 'Light', dark: 'Dark', system: 'System' }

function UserMenu({ email, onLogout }: { email: string; onLogout: () => void }) {
  const [open, setOpen] = useState(false)
  const [theme, setTheme] = useState<Theme>(getStoredTheme)
  const ref = useRef<HTMLDivElement>(null)
  const initials = (email.split('@')[0] || '?').slice(0, 2).toUpperCase()

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(v => !v)}
        className="flex items-center gap-2 rounded-full pl-1.5 pr-2.5 py-1.5 transition-colors"
        style={{ border: '1px solid var(--border)', background: 'var(--surface-1)', boxShadow: 'var(--shadow-xs)' }}
      >
        <span
          className="w-6 h-6 rounded-full flex items-center justify-center text-[11px] font-bold"
          style={{ background: 'var(--accent-tint)', color: 'var(--accent)' }}
        >
          {initials}
        </span>
        {/* Email label needs a wide viewport — at tablet widths it pushed the
            header row past the screen edge */}
        <span className="text-[13px] font-medium hidden lg:inline max-w-[140px] truncate" style={{ color: 'var(--text-muted)' }}>
          {email}
        </span>
        <ChevronDown size={13} style={{ color: 'var(--text-dim)' }} />
      </button>
      {open && (
        <div
          className="absolute right-0 z-50 overflow-hidden"
          style={{
            top: '115%', minWidth: 168, background: 'var(--surface-1)',
            border: '1px solid var(--border)', borderRadius: 14, boxShadow: 'var(--shadow-md)',
            animation: 'cr-pop .2s var(--ease-spring) both',
          }}
        >
          <button
            onClick={() => setTheme(cycleTheme())}
            className="flex items-center gap-2 w-full text-sm font-medium"
            style={{ padding: '11px 16px', color: 'var(--text)', background: 'none', border: 'none', cursor: 'pointer', borderBottom: '1px solid var(--border)' }}
          >
            {(() => { const Icon = THEME_ICON[theme]; return <Icon size={14} style={{ color: 'var(--text-muted)' }} /> })()}
            {THEME_LABEL[theme]}
          </button>
          <button
            onClick={() => { setOpen(false); onLogout() }}
            className="flex items-center gap-2 w-full text-sm font-medium"
            style={{ padding: '11px 16px', color: 'var(--text)', background: 'none', border: 'none', cursor: 'pointer' }}
          >
            <LogOut size={14} style={{ color: 'var(--text-muted)' }} /> Sign out
          </button>
        </div>
      )}
    </div>
  )
}

export default function App() {
  const { activeTab, setActiveTab, contacts, setContacts, token, userEmail, logout, resume, setResume, setAuth } = useStore()
  const [resumeReady, setResumeReady] = useState(false)

  // Keep-alive tabs: a tab mounts the first time it's visited and then STAYS
  // mounted (hidden with CSS) instead of unmounting on tab switch. Without this,
  // switching away from Compose/Send mid-operation unmounted the component and
  // killed the in-progress work — the bulk-generate loop, the send loop, their
  // progress state, and the React Query observers that persist results all died.
  // Now those operations keep running in the background while you're on another tab.
  const [mountedTabs, setMountedTabs] = useState<Set<TabId>>(() => new Set([activeTab]))
  useEffect(() => {
    setMountedTabs(prev => (prev.has(activeTab) ? prev : new Set(prev).add(activeTab)))
  }, [activeTab])

  // Hydrate contacts once at the top level. Without this, refreshing straight
  // into Compose/Send showed "No contacts yet" — those tabs read the store and
  // only Today/Hunt happened to fill it.
  useEffect(() => {
    if (!token) return
    contactsApi.list().then(setContacts).catch(() => {})
  }, [token]) // eslint-disable-line react-hooks/exhaustive-deps

  // Self-heal: a valid token with no cached email (cleared storage, imported
  // session) would greet the user as "there" — recover it from the API.
  useEffect(() => {
    if (!token || userEmail) return
    authApi.me().then(u => setAuth(token, u.email)).catch(() => {})
  }, [token, userEmail]) // eslint-disable-line react-hooks/exhaustive-deps

  // React to a global 401 (token expired) fired by the axios interceptor
  useEffect(() => {
    const onLogout = () => logout()
    window.addEventListener('coldreach:logout', onLogout)
    return () => window.removeEventListener('coldreach:logout', onLogout)
  }, [logout])

  // ── Tab ↔ URL hash sync ──────────────────────────────────────────────────
  // Gives every tab a shareable URL (#hunt), makes refresh keep your place,
  // and lets the browser back button move between tabs.
  useEffect(() => {
    const fromHash = () => {
      const h = window.location.hash.replace('#', '')
      if (isTabId(h)) setActiveTab(h)
    }
    fromHash()                                        // apply the initial URL
    window.addEventListener('hashchange', fromHash)
    return () => window.removeEventListener('hashchange', fromHash)
  }, [setActiveTab])

  useEffect(() => {
    if (window.location.hash.replace('#', '') !== activeTab) {
      window.location.hash = activeTab
    }
  }, [activeTab])

  // On login from a fresh browser the local store has no résumé, but the backend
  // may. Hydrate it so Compose works without forcing a re-upload.
  // Track readiness so Compose doesn't flash "no resume" while the fetch is in-flight.
  useEffect(() => {
    if (!token) return
    if (resume.trim()) { setResumeReady(true); return }
    resumeApi.getLatest()
      .then(r => { if (r.text?.trim()) setResume(r.text) })
      .catch(() => {})
      .finally(() => setResumeReady(true))
  }, [token]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!token) return <Auth />

  return (
    <div className="min-h-screen flex flex-col" style={{ background: 'var(--bg)' }}>
      {/* ── Frosted premium header ───────────────────────────────────────────── */}
      <header
        className="sticky top-0 z-40 flex items-center gap-3 sm:gap-4"
        style={{
          height: 64, padding: '0 clamp(12px, 3vw, 28px)',
          background: 'var(--header-bg)', backdropFilter: 'blur(12px)',
          borderBottom: '1px solid var(--border)', boxShadow: 'var(--shadow-xs)',
        }}
      >
        {/* Logo */}
        <div className="flex items-center gap-2.5 flex-shrink-0">
          <div
            className="flex items-center justify-center"
            style={{ width: 32, height: 32, borderRadius: 10, background: 'var(--accent)', boxShadow: 'var(--shadow-sm)' }}
          >
            <SendIcon size={15} color="#fff" />
          </div>
          {/* Wordmark hides on narrow phones so the tab nav keeps room */}
          <span className="hidden min-[480px]:inline" style={{ fontFamily: 'var(--font-display)', fontSize: 20, fontWeight: 800, letterSpacing: '-0.03em', color: 'var(--accent)', lineHeight: 1 }}>
            Cold<span style={{ color: 'var(--text)' }}>Reach</span>
          </span>
        </div>

        <div className="flex-1" />

        {/* Segmented pill nav — desktop/tablet only; phones get the bottom tab
            bar instead (a hidden-scrollbar overflow here made tabs invisible) */}
        <nav
          className="hidden md:inline-flex gap-0.5 max-w-full"
          style={{ padding: 3, background: 'var(--surface-2)', borderRadius: 'var(--radius-full)', border: '1px solid var(--border)' }}
        >
          {TABS.map(tab => {
            const active = activeTab === tab.id
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className="inline-flex items-center gap-1.5 text-[13px] font-semibold transition-all"
                style={{
                  // Narrower horizontal padding than the original 16px: at
                  // exactly 768px (the tablet breakpoint where this nav first
                  // appears) five tabs at 16px overflowed the header by ~22px.
                  padding: '7px 12px', borderRadius: 'var(--radius-full)', border: 'none', cursor: 'pointer',
                  background: active ? 'var(--surface-1)' : 'transparent',
                  color: active ? 'var(--text)' : 'var(--text-muted)',
                  boxShadow: active ? 'var(--shadow-sm)' : 'none',
                }}
              >
                {tab.num && (
                  <span style={{ fontSize: 11, fontWeight: 700, color: active ? 'var(--accent)' : 'var(--text-dim)' }}>{tab.num}</span>
                )}
                {tab.label}
                {tab.id === 'hunt' && contacts.length > 0 && (
                  <span
                    style={{
                      padding: '1px 6px', borderRadius: 'var(--radius-full)', fontSize: 11,
                      background: active ? 'var(--accent-tint)' : 'var(--surface-3)',
                      color: active ? 'var(--accent)' : 'var(--text-dim)',
                    }}
                  >
                    {contacts.length}
                  </span>
                )}
              </button>
            )
          })}
        </nav>

        <div className="flex-1" />

        {/* Right cluster — the contacts pill needs a wide viewport so the five
            nav tabs always win the space fight at tablet widths */}
        <div className="flex items-center gap-2.5 flex-shrink-0">
          <div
            className="hidden lg:flex items-center gap-1.5 text-[13px] font-medium"
            style={{ padding: '4px 10px', borderRadius: 'var(--radius-full)', background: 'var(--surface-2)', border: '1px solid var(--border)', color: 'var(--text-muted)' }}
          >
            <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--success)' }} />
            {contacts.length} contacts
          </div>
          <UserMenu email={userEmail} onLogout={logout} />
        </div>
      </header>

      {/* ── Content ──────────────────────────────────────────────────────────── */}
      {/* Bottom padding comes from classes (not the inline style) so mobile can
          reserve extra room for the fixed bottom tab bar. */}
      <main
        className="flex-1 w-full mx-auto pb-24 md:pb-16"
        style={{
          maxWidth: 960,
          paddingTop: 'clamp(20px, 4vw, 36px)',
          paddingLeft: 'clamp(14px, 3.5vw, 28px)',
          paddingRight: 'clamp(14px, 3.5vw, 28px)',
        }}
      >
        <ResumeReadyCtx.Provider value={resumeReady}>
          {(() => {
            const views: Record<TabId, ReactNode> = {
              today: <Today />, setup: <Setup />, hunt: <Hunt />, compose: <Compose />, send: <Send />,
            }
            return TABS.map(tab => {
              // Skip tabs never visited yet — lazy first mount keeps initial load light.
              if (!mountedTabs.has(tab.id)) return null
              const active = activeTab === tab.id
              return (
                // display:none (not unmount) keeps the tab's state and any in-flight
                // work alive while it's in the background.
                <div key={tab.id} style={{ display: active ? 'block' : 'none' }} aria-hidden={!active}>
                  {views[tab.id]}
                </div>
              )
            })
          })()}
        </ResumeReadyCtx.Provider>
      </main>

      {/* ── Mobile bottom tab bar (< md) ─────────────────────────────────────── */}
      <nav
        className="md:hidden fixed bottom-0 left-0 right-0 z-40 flex"
        aria-label="Primary"
        style={{
          background: 'var(--header-bg)', backdropFilter: 'blur(12px)',
          borderTop: '1px solid var(--border)',
          paddingBottom: 'env(safe-area-inset-bottom)',
        }}
      >
        {TABS.map(tab => {
          const Icon = TAB_ICONS[tab.id]
          const active = activeTab === tab.id
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              aria-current={active ? 'page' : undefined}
              className="flex-1 flex flex-col items-center gap-1 relative"
              style={{
                padding: '10px 0 8px', background: 'none', border: 'none', cursor: 'pointer',
                color: active ? 'var(--accent)' : 'var(--text-dim)',
              }}
            >
              <Icon size={18} strokeWidth={active ? 2.4 : 2} />
              <span style={{ fontSize: 10, fontWeight: active ? 700 : 500, letterSpacing: '0.01em' }}>
                {tab.label}
              </span>
              {tab.id === 'hunt' && contacts.length > 0 && (
                <span
                  className="absolute"
                  style={{
                    top: 4, right: '50%', marginRight: -20,
                    minWidth: 15, height: 15, padding: '0 4px',
                    borderRadius: 'var(--radius-full)', background: 'var(--accent)',
                    color: '#fff', fontSize: 9, fontWeight: 700,
                    display: 'flex', alignItems: 'center', justifyContent: 'center', lineHeight: 1,
                  }}
                >
                  {contacts.length > 99 ? '99+' : contacts.length}
                </span>
              )}
            </button>
          )
        })}
      </nav>
    </div>
  )
}
