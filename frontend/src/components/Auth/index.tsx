import { useEffect, useState } from 'react'
import toast from 'react-hot-toast'
import { LogIn, UserPlus } from 'lucide-react'
import Logo from '../shared/Logo'
import { GoogleLogin, type CredentialResponse } from '@react-oauth/google'
import { useStore } from '../../store'
import { authApi } from '../../api/auth'

// Only show the Google button when a client ID is configured at build/dev time.
// Without it the GoogleLogin widget can't render, so we fall back to email/password.
const GOOGLE_ENABLED = Boolean(import.meta.env.VITE_GOOGLE_CLIENT_ID)

export default function Auth({ initialMode = 'login', onBack }: {
  initialMode?: 'login' | 'register'
  // Present when Auth was reached from the landing page — renders a way back.
  onBack?: () => void
} = {}) {
  const { setAuth } = useStore()
  const [mode, setMode] = useState<'login' | 'register'>(initialMode)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  // A Google-only account can't use email/password. Rather than a transient red
  // error, we surface a persistent inline prompt pointing at the Google button.
  const [googleHint, setGoogleHint] = useState(false)

  // Fixed 304px overflowed 320px phones (288px available) — and a one-shot
  // read of innerWidth goes stale after phone rotation, so track resizes.
  const [googleWidth, setGoogleWidth] = useState(() => Math.min(304, window.innerWidth - 64))
  useEffect(() => {
    const onResize = () => setGoogleWidth(Math.min(304, window.innerWidth - 64))
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!email.trim() || !password) { toast.error('Enter email and password'); return }
    // Mirror the server's minimum up front — no round-trip just to learn
    // the password is too short.
    if (mode === 'register' && password.length < 8) {
      toast.error('Password must be at least 8 characters')
      return
    }
    setBusy(true)
    try {
      const res = mode === 'login'
        ? await authApi.login(email.trim(), password)
        : await authApi.register(email.trim(), password)
      setAuth(res.token, res.email)
      toast.success(mode === 'login' ? 'Welcome back' : 'Account created')
    } catch (err: any) {
      const msg = err?.message ?? 'Authentication failed'
      // The backend flags a Google-only account on both login and register with
      // "…Google Sign-In…". Guide the user to the right button instead of just
      // flashing an error they'll read as "login is broken".
      if (GOOGLE_ENABLED && /google sign-in/i.test(msg)) {
        setGoogleHint(true)
      } else {
        toast.error(msg)
      }
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
        {onBack && (
          <button
            onClick={onBack}
            className="text-[13px] font-semibold mb-4 hit-target"
            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', padding: 0 }}
          >
            ← Back
          </button>
        )}
        <div className="flex flex-col items-center mb-8">
          {/* Kit lockup: horizontal tile + lowercase cold↗reach */}
          <Logo size={40} wordmark />
          <p className="text-sm mt-3" style={{ color: 'var(--text-muted)' }}>
            Email the people who decide.
          </p>
        </div>

        <form onSubmit={submit} className="card space-y-4">
          {googleHint && GOOGLE_ENABLED && (
            <div
              role="status"
              style={{
                padding: '11px 13px', borderRadius: 10,
                background: 'var(--accent-tint)', border: '1px solid var(--accent)',
                color: 'var(--accent-text)', fontSize: 13, lineHeight: 1.5,
              }}
            >
              This email is registered with <strong>Google Sign-In</strong>, so it
              has no password. Use the <strong>Continue with Google</strong> button
              below — no password needed.
            </div>
          )}

          {GOOGLE_ENABLED && (
            <>
              <div
                className="flex justify-center"
                style={googleHint ? {
                  borderRadius: 10, padding: 4,
                  boxShadow: '0 0 0 2px var(--accent)',
                } : undefined}
              >
                <GoogleLogin
                  onSuccess={onGoogle}
                  onError={() => toast.error('Google sign-in failed')}
                  text="continue_with"
                  shape="rectangular"
                  width={String(googleWidth)}
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
                onClick={() => { setMode(m); setGoogleHint(false) }}
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
              onChange={e => { setEmail(e.target.value); setGoogleHint(false) }}
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
              minLength={mode === 'register' ? 8 : undefined}
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

          {/* Honest interim until an email-based reset exists — better than a
              dead end with no guidance at all. */}
          {mode === 'login' && (
            <details className="text-xs" style={{ color: 'var(--text-dim)' }}>
              <summary style={{ cursor: 'pointer', color: 'var(--text-muted)' }}>
                Forgot password?
              </summary>
              <p className="mt-1.5" style={{ lineHeight: 1.6 }}>
                {GOOGLE_ENABLED && 'If you signed up with Google, use the "Continue with Google" button above — no password needed. '}
                Automated password reset isn't available yet; contact whoever runs
                this ColdReach server to reset your account.
              </p>
            </details>
          )}

          <p className="text-xs text-center" style={{ color: 'var(--text-dim)' }}>
            Your data is private to your account.
          </p>
        </form>
      </div>
    </div>
  )
}
