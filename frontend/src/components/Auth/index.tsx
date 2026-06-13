import { useState } from 'react'
import toast from 'react-hot-toast'
import { LogIn, UserPlus } from 'lucide-react'
import { useStore } from '../../store'
import { authApi } from '../../api/auth'

export default function Auth() {
  const { setAuth } = useStore()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!email.trim() || !password) { toast.error('Enter email and password'); return }
    setBusy(true)
    try {
      const res = mode === 'login'
        ? await authApi.login(email.trim(), password)
        : await authApi.register(email.trim(), password)
      setAuth(res.token, res.email)
      toast.success(mode === 'login' ? 'Welcome back' : 'Account created')
    } catch (err: any) {
      toast.error(err.message ?? 'Authentication failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-4" style={{ background: 'var(--bg)' }}>
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <span className="text-3xl font-bold tracking-tight" style={{ fontFamily: 'Rajdhani', color: 'var(--accent)' }}>
            COLD<span style={{ color: 'var(--text)' }}>REACH</span>
          </span>
          <p className="text-xs font-mono mt-1" style={{ color: 'var(--text-dim)' }}>
            open-source cold outreach engine
          </p>
        </div>

        <form onSubmit={submit} className="card space-y-4">
          <div className="flex gap-1 p-1 rounded-lg" style={{ background: 'var(--surface-2)' }}>
            {(['login', 'register'] as const).map(m => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                className="flex-1 text-xs font-bold font-mono py-2 rounded-md transition-colors"
                style={{
                  background: mode === m ? 'var(--surface-1)' : 'transparent',
                  color: mode === m ? 'var(--accent)' : 'var(--text-dim)',
                }}
              >
                {m === 'login' ? 'LOG IN' : 'SIGN UP'}
              </button>
            ))}
          </div>

          <div>
            <label className="text-xs font-bold font-mono" style={{ color: 'var(--text-dim)' }}>EMAIL</label>
            <input
              type="email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              placeholder="you@example.com"
              className="input text-sm w-full mt-1"
              autoComplete="email"
            />
          </div>

          <div>
            <label className="text-xs font-bold font-mono" style={{ color: 'var(--text-dim)' }}>PASSWORD</label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder={mode === 'register' ? 'at least 8 characters' : '••••••••'}
              className="input text-sm w-full mt-1"
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            />
          </div>

          <button
            type="submit"
            disabled={busy}
            className="btn btn-primary w-full flex items-center justify-center gap-2 text-sm font-semibold"
          >
            {mode === 'login' ? <LogIn size={14} /> : <UserPlus size={14} />}
            {busy ? 'Please wait…' : mode === 'login' ? 'Log in' : 'Create account'}
          </button>

          <p className="text-xs text-center" style={{ color: 'var(--text-dim)' }}>
            Your data is private to your account.
          </p>
        </form>
      </div>
    </div>
  )
}
