import { useEffect } from 'react'
import { LogOut } from 'lucide-react'
import { useStore } from './store'
import Auth    from './components/Auth'
import Setup   from './components/Setup'
import Hunt    from './components/Hunt'
import Compose from './components/Compose'
import Send    from './components/Send'

const TABS = [
  { id: 'setup',   label: '01 Setup',   badge: null },
  { id: 'hunt',    label: '02 Hunt',    badge: 'contacts' },
  { id: 'compose', label: '03 Compose', badge: 'drafts'   },
  { id: 'send',    label: '04 Send',    badge: null        },
] as const

export default function App() {
  const { activeTab, setActiveTab, contacts, token, userEmail, logout } = useStore()

  // React to a global 401 (token expired) fired by the axios interceptor
  useEffect(() => {
    const onLogout = () => logout()
    window.addEventListener('coldreach:logout', onLogout)
    return () => window.removeEventListener('coldreach:logout', onLogout)
  }, [logout])

  if (!token) return <Auth />

  return (
    <div className="min-h-screen flex flex-col" style={{ background: 'var(--bg)' }}>

      {/* ── Header ──────────────────────────────────────────────────────────── */}
      <header className="flex items-center justify-between px-6 py-4 border-b" style={{ borderColor: 'var(--border)' }}>
        <div>
          <span className="text-xl font-bold tracking-tight" style={{ fontFamily: 'Rajdhani', color: 'var(--accent)' }}>
            COLD<span style={{ color: 'var(--text)' }}>REACH</span>
          </span>
          <span className="ml-3 text-xs font-mono" style={{ color: 'var(--text-dim)' }}>
            open-source cold outreach engine
          </span>
        </div>
        <div className="flex items-center gap-4">
          <span className="text-xs font-mono" style={{ color: 'var(--text-dim)' }}>
            {contacts.length} contacts
          </span>
          {userEmail && (
            <span className="text-xs font-mono hidden sm:inline" style={{ color: 'var(--text-dim)' }}>
              {userEmail}
            </span>
          )}
          <button
            onClick={logout}
            title="Log out"
            className="flex items-center gap-1 text-xs font-mono px-2 py-1 rounded transition-colors"
            style={{ color: 'var(--text-dim)' }}
          >
            <LogOut size={12} /> Logout
          </button>
        </div>
      </header>

      {/* ── Tab bar ─────────────────────────────────────────────────────────── */}
      <nav className="flex gap-1 px-6 pt-4">
        {TABS.map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className="px-4 py-2 text-xs font-bold font-mono tracking-wider rounded-t-lg border-b-2 transition-all"
            style={{
              borderBottomColor: activeTab === tab.id ? 'var(--accent)' : 'transparent',
              color: activeTab === tab.id ? 'var(--accent)' : 'var(--text-dim)',
              background: activeTab === tab.id ? 'var(--surface-1)' : 'transparent',
            }}
          >
            {tab.label}
            {tab.badge === 'contacts' && contacts.length > 0 && (
              <span className="ml-2 px-1.5 py-0.5 rounded text-xs" style={{ background: 'var(--accent-dim)', color: 'var(--accent)' }}>
                {contacts.length}
              </span>
            )}
          </button>
        ))}
      </nav>

      {/* ── Content ─────────────────────────────────────────────────────────── */}
      <main className="flex-1 px-6 pb-10 pt-6 max-w-4xl mx-auto w-full">
        {activeTab === 'setup'   && <Setup />}
        {activeTab === 'hunt'    && <Hunt />}
        {activeTab === 'compose' && <Compose />}
        {activeTab === 'send'    && <Send />}
      </main>
    </div>
  )
}
