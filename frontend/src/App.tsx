import { useEffect, useState, useRef, createContext } from 'react'
import { LogOut, Send as SendIcon, ChevronDown } from 'lucide-react'
import { useStore } from './store'
import { resumeApi } from './api/resume'
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

function UserMenu({ email, onLogout }: { email: string; onLogout: () => void }) {
  const [open, setOpen] = useState(false)
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
        <span className="text-[13px] font-medium hidden sm:inline max-w-[140px] truncate" style={{ color: 'var(--text-muted)' }}>
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
  const { activeTab, setActiveTab, contacts, token, userEmail, logout, resume, setResume } = useStore()
  const [resumeReady, setResumeReady] = useState(false)

  // React to a global 401 (token expired) fired by the axios interceptor
  useEffect(() => {
    const onLogout = () => logout()
    window.addEventListener('coldreach:logout', onLogout)
    return () => window.removeEventListener('coldreach:logout', onLogout)
  }, [logout])

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
        className="sticky top-0 z-40 flex items-center gap-4"
        style={{
          height: 64, padding: '0 28px',
          background: 'rgba(250,247,242,0.92)', backdropFilter: 'blur(12px)',
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
          <span style={{ fontFamily: 'var(--font-display)', fontSize: 20, fontWeight: 800, letterSpacing: '-0.03em', color: 'var(--accent)', lineHeight: 1 }}>
            Cold<span style={{ color: 'var(--text)' }}>Reach</span>
          </span>
        </div>

        <div className="flex-1" />

        {/* Segmented pill nav — scrolls horizontally on narrow screens instead
            of overflowing the fixed-height header */}
        <nav
          className="inline-flex gap-0.5 max-w-full overflow-x-auto"
          style={{ padding: 3, background: 'var(--surface-2)', borderRadius: 'var(--radius-full)', border: '1px solid var(--border)', scrollbarWidth: 'none' }}
        >
          {TABS.map(tab => {
            const active = activeTab === tab.id
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className="inline-flex items-center gap-1.5 text-[13px] font-semibold transition-all"
                style={{
                  padding: '7px 16px', borderRadius: 'var(--radius-full)', border: 'none', cursor: 'pointer',
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

        {/* Right cluster */}
        <div className="flex items-center gap-2.5 flex-shrink-0">
          <div
            className="hidden md:flex items-center gap-1.5 text-[13px] font-medium"
            style={{ padding: '4px 10px', borderRadius: 'var(--radius-full)', background: 'var(--surface-2)', border: '1px solid var(--border)', color: 'var(--text-muted)' }}
          >
            <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--success)' }} />
            {contacts.length} contacts
          </div>
          <UserMenu email={userEmail} onLogout={logout} />
        </div>
      </header>

      {/* ── Content ──────────────────────────────────────────────────────────── */}
      <main className="flex-1 w-full mx-auto" style={{ maxWidth: 960, padding: '36px 28px 60px' }}>
        <ResumeReadyCtx.Provider value={resumeReady}>
          {activeTab === 'today'   && <Today />}
          {activeTab === 'setup'   && <Setup />}
          {activeTab === 'hunt'    && <Hunt />}
          {activeTab === 'compose' && <Compose />}
          {activeTab === 'send'    && <Send />}
        </ResumeReadyCtx.Provider>
      </main>
    </div>
  )
}
