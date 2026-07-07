import { useState } from 'react'
import toast from 'react-hot-toast'
import { LogIn, UserPlus, Send as SendIcon } from 'lucide-react'
import { GoogleLogin, type CredentialResponse } from '@react-oauth/google'
import { useStore } from '../../store'
import { authApi } from '../../api/auth'

// Only show the Google button when a client ID is configured at build/dev time.
// Without it the GoogleLogin widget can't render, so we fall back to email/password.
const GOOGLE_ENABLED = Boolean(import.meta.env.VITE_GOOGLE_CLIENT_ID)

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

  const onGoogle = async (cred: CredentialResponse) => {
    if (!cred.credential) { toast.error('Google sign-in failed'); return }
    setBusy(true)
    try {
      const res = await authApi.google(cred.credential)
      setAuth(res.token, res.email)
      toast.success('Welcome')
    } catch (err: any) {
      toast.error(err.message ?? 'Google sign-in failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-4" style={{ background: 'var(--bg)' }}>
      <div className="w-full max-w-sm">
        <div className="flex flex-col items-center mb-8">
          <div
            className="flex items-center justify-center mb-3"
            style={{ width: 44, height: 44, borderRadius: 14, background: 'var(--accent)', boxShadow: 'var(--shadow-sm)' }}
          >
            <SendIcon size={20} color="#fff" />
          </div>
          <span style={{ fontFamily: 'var(--font-display)', fontSize: 30, fontWeight: 800, letterSpacing: '-0.03em', color: 'var(--accent)', lineHeight: 1 }}>
            Cold<span style={{ color: 'var(--text)' }}>Reach</span>
          </span>
          <p className="text-sm mt-2" style={{ color: 'var(--text-muted)' }}>
            open-source cold outreach engine
          </p>
        </div>

        <form onSubmit={submit} className="card space-y-4">
          {GOOGLE_ENABLED && (
            <>
              <div className="flex justify-center">
                <GoogleLogin
                  onSuccess={onGoogle}
                  onError={() => toast.error('Google sign-in failed')}
                  text="continue_with"
                  shape="rectangular"
                  // Fixed 304px overflowed 320px phones (288px available)
                  width={String(Math.min(304, window.innerWidth - 64))}
                />
              </div>
              <div className="flex items-center gap-3">
                <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
                <span className="text-xs font-medium" style={{ color: 'var(--text-dim)' }}>or</span>
                <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
              </div>
            </>
          )}

          <div className="flex gap-1 p-1 rounded-lg" style={{ background: 'var(--surface-2)' }}>
            {(['login', 'register'] as const).map(m => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                className="flex-1 text-sm font-semibold py-2 rounded-md transition-colors"
                style={{
                  background: mode === m ? 'var(--surface-1)' : 'transparent',
                  color: mode === m ? 'var(--accent)' : 'var(--text-muted)',
                  boxShadow: mode === m ? 'var(--shadow-xs)' : 'none',
                }}
              >
                {m === 'login' ? 'Log in' : 'Sign up'}
              </button>
            ))}
          </div>

          <div>
            <label className="text-[13px] font-semibold" style={{ color: 'var(--text-muted)' }}>Email</label>
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
            <label className="text-[13px] font-semibold" style={{ color: 'var(--text-muted)' }}>Password</label>
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
