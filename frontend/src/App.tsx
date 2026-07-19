import { useEffect, useState, useRef, createContext, lazy, Suspense, Component, type ReactNode } from 'react'
import { LogOut, Send as SendIcon, ChevronDown, Home, Settings, Search, Wand2, Sun, Moon, Monitor, Inbox, BarChart3 } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { Analytics } from '@vercel/analytics/react'
import { useStore } from './store'
import { resumeApi } from './api/resume'
import { authApi } from './api/auth'
import { useContacts } from './hooks/useContacts'
import { useAutomationConfig } from './hooks/useAutomationConfig'
import { useAutoReplyCheck } from './hooks/useAutoReplyCheck'
import { getStoredTheme, cycleTheme, type Theme } from './lib/theme'
import Auth    from './components/Auth'
import Logo    from './components/shared/Logo'
import Sidebar from './components/Sidebar'
import CommandPalette, { type Command } from './components/shared/CommandPalette'
// Tab views are code-split: each loads its own chunk on first visit (paired
// with the mountedTabs gating below), keeping the initial bundle to the shell
// + whichever tab opens first. Auth stays eager — it's the shared login paint;
// Landing is its own chunk so returning (logged-in) users never download it.
const Landing   = lazy(() => import('./components/Landing'))
const Setup     = lazy(() => import('./components/Setup'))
const Hunt      = lazy(() => import('./components/Hunt'))
const Compose   = lazy(() => import('./components/Compose'))
const Send      = lazy(() => import('./components/Send'))
const Today     = lazy(() => import('./components/Today'))
const Replies   = lazy(() => import('./components/Replies'))
const AnalyticsView = lazy(() => import('./components/Analytics'))

type TabId = 'today' | 'setup' | 'hunt' | 'compose' | 'send' | 'replies' | 'analytics'

export const ResumeReadyCtx = createContext(false)

// Brief fallback shown while a lazily-loaded tab chunk downloads on first visit.
function TabLoading() {
  return (
    <div className="flex items-center justify-center py-20" aria-live="polite" aria-busy="true">
      <div
        className="w-6 h-6 rounded-full animate-spin"
        style={{ border: '2px solid var(--border)', borderTopColor: 'var(--accent)' }}
      />
    </div>
  )
}

// Catches a tab chunk that fails to load — which happens whenever a new deploy
// goes live while the app is open (Vercel stops serving the old hashed assets,
// so the first visit to a not-yet-loaded tab 404s). Without this boundary the
// rejection climbs past Suspense to the root ErrorBoundary and unmounts the
// WHOLE app, killing in-flight work in other tabs. Scoped per-tab, the other
// tabs keep running and the user gets a one-click refresh.
class TabErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  componentDidCatch(error: unknown) { console.error('ColdReach tab load error:', error) }
  render() {
    if (!this.state.failed) return this.props.children
    return (
      <div className="flex flex-col items-center justify-center py-20 px-6 text-center">
        <p className="text-sm mb-4" style={{ color: 'var(--text-muted)' }}>
          ColdReach was updated — refresh to load the newest version.
        </p>
        <button
          onClick={() => window.location.reload()}
          className="px-5 py-2 rounded-full text-sm font-bold"
          style={{ background: 'var(--accent)', color: 'var(--on-accent)', border: 'none', cursor: 'pointer' }}
        >
          Refresh
        </button>
      </div>
    )
  }
}

// shortLabel is for the mobile bottom tab bar only — five equal-width flex
// columns on a ~375px screen give each label ~75px, and "Email Generation"
// visibly wraps to multiple lines there. Desktop/tablet nav and every page
// header always show the full `label`.
// `mobile: false` keeps an entry out of the phone bottom bar (five slots max
// on ~375px): Setup moves to a gear in the mobile header, Analytics is
// reachable from the Dashboard's "All analytics →" link.
const TABS: Array<{ id: TabId; num: string | null; label: string; shortLabel?: string; mobile?: boolean }> = [
  { id: 'today',     num: null, label: 'Dashboard'        },
  { id: 'setup',     num: '01', label: 'Profile Setup',     shortLabel: 'Profile', mobile: false },
  { id: 'hunt',      num: '02', label: 'Hunt'             },
  { id: 'compose',   num: '03', label: 'Email Generation',  shortLabel: 'Emails'  },
  { id: 'send',      num: '04', label: 'Send Mail',         shortLabel: 'Send'   },
  { id: 'replies',   num: null, label: 'Replies'          },
  { id: 'analytics', num: null, label: 'Analytics',                               mobile: false },
]

const TAB_IDS = TABS.map(t => t.id)
const isTabId = (v: string): v is TabId => (TAB_IDS as string[]).includes(v)

// Icons for the mobile bottom tab bar
const TAB_ICONS: Record<TabId, LucideIcon> = {
  today: Home, setup: Settings, hunt: Search, compose: Wand2, send: SendIcon,
  replies: Inbox, analytics: BarChart3,
}

const THEME_ICON: Record<Theme, typeof Sun> = { light: Sun, dark: Moon, system: Monitor }
const THEME_LABEL: Record<Theme, string> = { light: 'Light', dark: 'Dark', system: 'System' }

function UserMenu({ email, onLogout }: { email: string; onLogout: () => void }) {
  const [open, setOpen] = useState(false)
  const [theme, setTheme] = useState<Theme>(getStoredTheme)
  const ref = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const initials = (email.split('@')[0] || '?').slice(0, 2).toUpperCase()

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  // Escape closes the menu and hands focus back to the trigger — without the
  // focus return, keyboard users are dropped at the document root.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false)
        triggerRef.current?.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open])

  return (
    <div ref={ref} className="relative">
      <button
        ref={triggerRef}
        aria-haspopup="menu"
        aria-expanded={open}
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
  const { activeTab, setActiveTab, contacts, token, userEmail, logout, resume, setResume, setAuth } = useStore()
  const [resumeReady, setResumeReady] = useState(false)
  // Logged-out flow: marketing landing first; Log in / Sign up switch to Auth.
  const [authView, setAuthView] = useState<'landing' | 'login' | 'register'>('landing')
  const [paletteOpen, setPaletteOpen] = useState(false)

  // ⌘K / Ctrl+K toggles the command palette anywhere in the logged-in app.
  useEffect(() => {
    if (!token) return
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen(v => !v)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [token])

  // Hydrate contacts once at the top level via the SHARED query (Today and Hunt
  // reuse the same cache instead of firing their own fetches). Without this,
  // refreshing straight into Compose/Send showed "No contacts yet".
  useContacts(!!token)

  // Server config (Gmail connection) powers the background reply check: when
  // creds are stored server-side, quietly sync the inbox on app open (throttled)
  // so "new replies" alerts are fresh without pressing "Check Replies".
  const { data: appConfig } = useAutomationConfig(!!token)
  useAutoReplyCheck(!!token && !!appConfig?.has_gmail)

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
    if (!window.location.hash) {
      // First load on a bare URL: replace, don't push — assigning the hash here
      // would add a history entry, making the first Back press a no-op ghost.
      history.replaceState(null, '', '#' + activeTab)
    } else if (window.location.hash.slice(1) !== activeTab) {
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

  if (!token) {
    if (authView === 'landing') {
      return (
        <Suspense fallback={<TabLoading />}>
          <Landing onLogin={() => setAuthView('login')} onSignup={() => setAuthView('register')} />
        </Suspense>
      )
    }
    return <Auth initialMode={authView} onBack={() => setAuthView('landing')} />
  }

  // Sidebar meter: sends made today vs the pacing cap (backend caps at 25/day).
  const todayStr = new Date().toDateString()
  const sentToday = contacts.filter(c =>
    c.last_emailed_at && new Date(c.last_emailed_at).toDateString() === todayStr
  ).length

  const commands: Command[] = [
    ...TABS.map(tab => ({
      id: tab.id, icon: TAB_ICONS[tab.id], label: tab.label, kind: 'view',
      run: () => setActiveTab(tab.id),
    })),
    { id: 'signout', icon: LogOut, label: 'Sign out', kind: 'action', run: logout },
  ]

  return (
    <div className="min-h-screen flex flex-col md:pl-[232px]" style={{ background: 'var(--bg)' }}>
      {/* ── v2 shell: persistent sidebar on desktop ─────────────────────────── */}
      <Sidebar
        items={TABS.map(tab => ({
          id: tab.id, icon: TAB_ICONS[tab.id], label: tab.label,
          badge: tab.id === 'hunt' ? contacts.length : undefined,
        }))}
        activeId={activeTab}
        onSelect={id => { if (isTabId(id)) setActiveTab(id) }}
        onOpenPalette={() => setPaletteOpen(true)}
        sentToday={sentToday}
        sendCap={25}
        email={userEmail}
        onLogout={logout}
      />
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} commands={commands} />

      {/* ── Slim header — phones only (desktop gets the sidebar) ─────────────── */}
      <header
        className="md:hidden sticky top-0 z-40 flex items-center justify-between"
        style={{
          height: 60, padding: '0 clamp(12px, 3vw, 28px)',
          background: 'var(--header-bg)', backdropFilter: 'blur(12px)',
          borderBottom: '1px solid var(--border)', boxShadow: 'var(--shadow-xs)',
        }}
      >
        <Logo size={28} wordmark />
        <div className="flex items-center gap-2">
          {/* Setup lives here on phones — the bottom bar's five slots go to
              the daily-loop views (Replies took Profile's place). */}
          <button
            onClick={() => setActiveTab('setup')}
            aria-label="Profile Setup"
            aria-current={activeTab === 'setup' ? 'page' : undefined}
            className="hit-target flex items-center justify-center"
            style={{
              width: 34, height: 34, borderRadius: 10, border: '1px solid var(--border)',
              background: activeTab === 'setup' ? 'var(--accent-tint)' : 'var(--surface-1)',
              color: activeTab === 'setup' ? 'var(--accent-text)' : 'var(--text-muted)',
              cursor: 'pointer',
            }}
          >
            <Settings size={16} />
          </button>
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
              replies: <Replies />, analytics: <AnalyticsView />,
            }
            return TABS.map(tab => {
              // Skip tabs never visited yet — lazy first mount keeps initial load light.
              if (!mountedTabs.has(tab.id)) return null
              const active = activeTab === tab.id
              return (
                // display:none (not unmount) keeps the tab's state and any in-flight
                // work alive while it's in the background.
                <div key={tab.id} style={{ display: active ? 'block' : 'none' }} aria-hidden={!active}>
                  <TabErrorBoundary>
                    <Suspense fallback={<TabLoading />}>
                      {views[tab.id]}
                    </Suspense>
                  </TabErrorBoundary>
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
        {TABS.filter(tab => tab.mobile !== false).map(tab => {
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
                color: active ? 'var(--accent)' : 'var(--text-muted)',
              }}
            >
              <Icon size={18} strokeWidth={active ? 2.4 : 2} />
              <span style={{ fontSize: 10, fontWeight: active ? 700 : 500, letterSpacing: '0.01em', whiteSpace: 'nowrap' }}>
                {tab.shortLabel ?? tab.label}
              </span>
              {tab.id === 'hunt' && contacts.length > 0 && (
                <span
                  className="absolute tnum"
                  style={{
                    top: 4, right: '50%', marginRight: -20,
                    minWidth: 15, height: 15, padding: '0 4px',
                    borderRadius: 'var(--radius-full)', background: 'var(--accent)',
                    color: 'var(--on-accent)', fontSize: 9, fontWeight: 700,
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
      <Analytics />
    </div>
  )
}
