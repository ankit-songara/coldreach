import { useState, useCallback, useEffect } from 'react'
import { useDropzone } from 'react-dropzone'
import { useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Eye, EyeOff, CheckCircle2, ExternalLink, ArrowRight, Save, Pencil, ChevronDown, ChevronRight, Mail } from 'lucide-react'
import { useStore } from '../../store'
import { resumeApi } from '../../api/resume'
import { automationApi } from '../../api/automation'
import { useAutomationConfig } from '../../hooks/useAutomationConfig'
import api from '../../api/client'

// The backend stores the signature links as ONE line ("a · b · c").
// The UI edits them as three labeled fields — split/join at the boundary.
function splitLinks(line: string): { linkedin: string; github: string; portfolio: string } {
  const out = { linkedin: '', github: '', portfolio: '' }
  for (const part of (line || '').split('·').map(s => s.trim()).filter(Boolean)) {
    const p = part.toLowerCase()
    if (p.includes('linkedin') && !out.linkedin) out.linkedin = part
    else if (p.includes('github') && !out.github) out.github = part
    else if (!out.portfolio) out.portfolio = part
  }
  return out
}
const joinLinks = (l: { linkedin: string; github: string; portfolio: string }) =>
  [l.linkedin, l.github, l.portfolio].map(s => s.trim()).filter(Boolean).join(' · ')

export default function Setup() {
  const { resume, setResume, gmailAddress, gmailAppPassword, setGmailCreds, setActiveTab } = useStore()
  const [extracting, setExtracting] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const [testing, setTesting] = useState(false)
  const [savingResume, setSavingResume] = useState(false)
  const [localAddress, setLocalAddress]   = useState(gmailAddress)
  const [localPassword, setLocalPassword] = useState(gmailAppPassword)
  const [senderName, setSenderName]       = useState('')
  const [links, setLinks]                 = useState({ linkedin: '', github: '', portfolio: '' })
  const [savingName, setSavingName]       = useState(false)
  const [editingSig, setEditingSig]       = useState(false)
  const [gmailConnected, setGmailConnected] = useState(false)
  const [updatingCreds, setUpdatingCreds]   = useState(false)  // show form while connected
  const [appPwOpen, setAppPwOpen]           = useState(false)  // "Use an App Password instead" expander
  const [startingOauth, setStartingOauth]   = useState(false)

  const qc = useQueryClient()
  // Server config comes from the shared query (same cache as Today and Send).
  const { data: cfg } = useAutomationConfig()

  const gmailMethod    = cfg?.gmail_method ?? ''
  const oauthAvailable = cfg?.oauth_available ?? false

  // The OAuth callback lands the browser back here as /?gmail=<result>#setup
  // (hash routing — the param precedes the #). Toast once, then strip the
  // param so a refresh doesn't re-toast.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const result = params.get('gmail')
    if (!result) return
    if (result === 'connected') {
      toast.success('Gmail connected — you can send right away')
      qc.invalidateQueries({ queryKey: ['config'] })
    } else if (result === 'cancelled') {
      toast('Gmail connection cancelled — nothing was changed', { icon: '↩️' })
    } else if (result === 'error') {
      toast.error('Google connection failed — please try again')
    }
    params.delete('gmail')
    const rest = params.toString()
    history.replaceState(null, '',
      window.location.pathname + (rest ? `?${rest}` : '') + window.location.hash)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Populate the local form state from the resolved signature (explicit
  // override → auto-detected from résumé). Guarded by !editingSig so a
  // background refetch never stomps on text the user is typing.
  useEffect(() => {
    if (!cfg || editingSig) return
    setSenderName(cfg.sender_name || '')
    setLinks(splitLinks(cfg.signature_links || ''))
    setGmailConnected(cfg.has_gmail)
    setLocalAddress(prev => prev || cfg.gmail_address || '')
  }, [cfg, editingSig])

  // Re-detect after the résumé changes — a new upload may carry new links/name.
  const refreshSignatureFromResume = () => { qc.invalidateQueries({ queryKey: ['config'] }) }

  const onDrop = useCallback(async (files: File[]) => {
    const file = files[0]
    if (!file) return
    setExtracting(true)
    try {
      const form = new FormData()
      form.append('file', file)
      const { data } = await api.post<{ text: string }>('/resume/extract', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setResume(data.text)
      // Persist server-side immediately — before this, the extracted text only
      // lived in localStorage until the user separately clicked "Save Resume",
      // and logging out (or switching devices) silently lost it.
      try {
        await resumeApi.save(data.text)
        refreshSignatureFromResume()
        toast.success(`Resume saved — ${data.text.length.toLocaleString()} chars from ${file.name}`)
      } catch {
        refreshSignatureFromResume()
        toast(`Extracted ${file.name} — click "Save Resume" to store it`, { icon: '⚠️' })
      }
    } catch (e: any) {
      toast.error(e.message)
    } finally {
      setExtracting(false)
    }
  }, [setResume]) // eslint-disable-line react-hooks/exhaustive-deps

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'application/pdf': ['.pdf'],
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
    },
    maxFiles: 1,
    // Without this, dropping 2+ files (or a wrong file type) rejects
    // everything and silently fires nothing — onDrop's accepted-files array
    // is empty and the whole thing looks like the click did nothing at all.
    onDropRejected: (rejections) => {
      if (rejections.length > 1) {
        toast.error('Drop just one file — pick a single PDF or DOCX résumé.')
      } else {
        toast.error(rejections[0]?.errors[0]?.message || 'That file type isn\'t supported. Use PDF or DOCX.')
      }
    },
  })

  // Verify against Gmail, then store server-side (App Password encrypted at
  // rest, never sent back). Sending and reply-checks work from any device
  // afterwards without re-entering anything.
  const handleConnect = async () => {
    if (!localAddress || !localPassword) {
      toast.error('Enter your Gmail address and App Password first')
      return
    }
    setTesting(true)
    try {
      const fresh = await automationApi.saveGmail(localAddress.trim(), localPassword.trim())
      qc.setQueryData(['config'], fresh)   // Today + Send update instantly
      setGmailConnected(fresh.has_gmail)
      setGmailCreds(localAddress.trim(), localPassword.trim())
      setLocalPassword('')
      setUpdatingCreds(false)
      toast.success('Gmail connected — credentials saved securely')
    } catch (e: any) {
      toast.error(e.message)
    } finally {
      setTesting(false)
    }
  }

  // One click, no password — fetch the Google consent URL and send the whole
  // page there. On success the backend redirects back with ?gmail=connected.
  const handleOauthStart = async () => {
    setStartingOauth(true)
    try {
      const { url } = await automationApi.gmailOauthStart()
      window.location.href = url
      // no setStartingOauth(false) — the page is navigating away
    } catch (e: any) {
      toast.error(e.message)
      setStartingOauth(false)
    }
  }

  const handleDisconnect = async () => {
    try {
      // OAuth grants and stored App Passwords live behind different endpoints.
      const fresh = gmailMethod === 'oauth'
        ? await automationApi.gmailOauthDisconnect()
        : await automationApi.deleteGmail()
      qc.setQueryData(['config'], fresh)
      setGmailConnected(fresh.has_gmail)
      setGmailCreds('', '')
      setLocalPassword('')
      toast('Gmail disconnected', { icon: '🔌' })
    } catch (e: any) {
      toast.error(e.message)
    }
  }

  const handleSaveName = async () => {
    setSavingName(true)
    try {
      const fresh = await automationApi.setProfile(senderName.trim(), joinLinks(links))
      qc.setQueryData(['config'], fresh)
      setSenderName(fresh.sender_name || '')
      setLinks(splitLinks(fresh.signature_links || ''))
      setEditingSig(false)
      toast.success('Signature saved')
    } catch (e: any) {
      toast.error(e.message)
    } finally {
      setSavingName(false)
    }
  }

  const handleSaveResume = async () => {
    if (!resume.trim()) { toast.error('Resume text is empty'); return }
    setSavingResume(true)
    try {
      await resumeApi.save(resume)
      refreshSignatureFromResume()
      toast.success('Resume saved')
    } catch (e: any) {
      toast.error(e.message)
    } finally {
      setSavingResume(false)
    }
  }

  // The full App Password form — rendered standalone when OAuth isn't
  // available (exactly today's UI), or inside the "Use an App Password
  // instead" expander when it is.
  const appPasswordForm = (
    <div className="card space-y-3">
      <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
        Without Gmail connected you can still hunt contacts, generate emails, and
        send each one from your own Gmail with one click. Connecting enables
        in-app sending ("Send All" and per-contact Send) plus reply tracking.
      </p>
      <div className="space-y-1">
        <label className="text-xs font-mono" style={{ color: 'var(--text-muted)' }}>Gmail address</label>
        <input
          type="email"
          value={localAddress}
          onChange={e => setLocalAddress(e.target.value)}
          placeholder="you@gmail.com"
          className="input text-sm w-full"
        />
      </div>

      <div className="space-y-1">
        <label className="text-xs font-mono" style={{ color: 'var(--text-muted)' }}>App Password</label>
        <div className="relative">
          <input
            type={showPassword ? 'text' : 'password'}
            value={localPassword}
            onChange={e => setLocalPassword(e.target.value)}
            placeholder="xxxx xxxx xxxx xxxx"
            className="input text-sm w-full pr-10"
          />
          <button
            type="button"
            onClick={() => setShowPassword(p => !p)}
            className="absolute right-3 top-1/2 -translate-y-1/2"
            style={{ color: 'var(--text-dim)' }}
          >
            {showPassword ? <EyeOff size={14} /> : <Eye size={14} />}
          </button>
        </div>
      </div>

      {/* Instructions */}
      <div className="rounded-lg p-3 text-xs space-y-1"
        style={{ background: 'color-mix(in srgb, var(--accent) 5%, transparent)', border: '1px solid color-mix(in srgb, var(--accent) 12%, transparent)', color: 'var(--text-muted)' }}>
        <p className="font-semibold" style={{ color: 'var(--text)' }}>How to get an App Password:</p>
        <p>1. Enable 2-Step Verification on your Google account</p>
        <p>2. Go to{' '}
          <a
            href="https://myaccount.google.com/apppasswords"
            target="_blank"
            rel="noreferrer"
            style={{ color: 'var(--accent)', textDecoration: 'underline' }}
          >
            myaccount.google.com/apppasswords
          </a>
        </p>
        <p>3. Create a new app → copy the 16-character password</p>
        <a
          href="https://myaccount.google.com/apppasswords"
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-1 mt-1"
          style={{ color: 'var(--accent)' }}
        >
          Open App Passwords <ExternalLink size={10} />
        </a>
        <p className="pt-1" style={{ color: 'var(--text-muted)' }}>
          🔒 Verified with Gmail, then stored encrypted on the server — never in
          this browser. Remove it anytime with Disconnect.
        </p>
      </div>

      <div className="flex gap-2">
        <button
          onClick={handleConnect}
          disabled={testing || !localAddress || !localPassword}
          className="btn text-sm flex-1"
          style={{
            background: 'var(--accent-dim)',
            borderColor: 'color-mix(in srgb, var(--accent) 25%, transparent)',
            color: 'var(--accent-text)',
            opacity: testing || !localAddress || !localPassword ? 0.5 : 1,
          }}
        >
          {testing ? 'Verifying…' : 'Verify & Save'}
        </button>
        {updatingCreds && (
          <button
            onClick={() => { setUpdatingCreds(false); setLocalPassword('') }}
            disabled={testing}
            className="btn text-sm"
            style={{ color: 'var(--text-muted)', borderColor: 'var(--border)' }}
          >
            Cancel
          </button>
        )}
      </div>
    </div>
  )

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold tracking-wide mb-1" style={{ fontFamily: 'var(--font-display)' }}>Profile Setup</h1>
        <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
          Add your resume once — that's all you need to start. Connect Gmail only
          if you want to send emails directly from ColdReach.
        </p>
      </div>

      {/* ── Resume ────────────────────────────────────────────────────────── */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-xs font-bold font-mono tracking-widest" style={{ color: 'var(--text-muted)' }}>
            RESUME
          </span>
          {resume && (
            <span className="text-xs font-mono" style={{ color: 'var(--accent)' }}>
              ✓ {resume.length.toLocaleString()} chars
            </span>
          )}
        </div>

        <div
          {...getRootProps()}
          className="rounded-xl border-2 border-dashed p-10 text-center cursor-pointer transition-colors"
          style={{
            borderColor: isDragActive ? 'var(--accent)' : 'var(--border)',
            background: isDragActive ? 'var(--accent-dim)' : 'var(--surface-1)',
          }}
        >
          <input {...getInputProps()} />
          <p className="text-sm font-medium">
            {extracting ? '⏳ Extracting...' : isDragActive ? 'Drop it!' : 'Drop PDF or DOCX — or click to browse'}
          </p>
          <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>Max 15 MB</p>
        </div>

        <textarea
          value={resume}
          onChange={e => setResume(e.target.value)}
          placeholder="Paste your resume text here, or upload a file above..."
          rows={14}
          className="input font-mono text-xs resize-none"
          style={{ lineHeight: '1.7' }}
        />

        <div className="flex gap-2 justify-end">
          <button
            onClick={handleSaveResume}
            disabled={savingResume || !resume.trim()}
            className="btn text-sm flex items-center gap-1.5"
            style={{
              background: 'color-mix(in srgb, var(--success) 10%, transparent)',
              borderColor: 'color-mix(in srgb, var(--success) 25%, transparent)',
              color: 'var(--success-text)',
              opacity: savingResume || !resume.trim() ? 0.5 : 1,
            }}
          >
            <Save size={13} />
            {savingResume ? 'Saving…' : 'Save Resume'}
          </button>
        </div>
      </div>

      {/* ── Signature (auto-detected; preview-first, edit on demand) ──────── */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-xs font-bold font-mono tracking-widest" style={{ color: 'var(--text-muted)' }}>
            EMAIL SIGNATURE
          </span>
          {!editingSig && (
            <button
              onClick={() => setEditingSig(true)}
              className="flex items-center gap-1 text-xs font-semibold"
              style={{ color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer' }}
            >
              <Pencil size={11} /> Edit
            </button>
          )}
        </div>

        {!editingSig ? (
          <div className="card space-y-3">
            <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
              Detected automatically from your résumé and added to the bottom of every email.
              Wrong or missing something? Hit Edit.
            </p>
            <div
              className="text-sm"
              style={{ padding: '12px 16px', background: 'var(--surface-2)', borderRadius: 10, lineHeight: 1.8 }}
            >
              <span style={{ color: 'var(--text-muted)' }}>Best regards,</span><br />
              <span className="font-semibold">{senderName || 'Your name'}</span><br />
              {joinLinks(links)
                ? <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{joinLinks(links)}</span>
                : <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                    No links found in your résumé — add LinkedIn / GitHub / portfolio via Edit.
                  </span>}
            </div>
          </div>
        ) : (
          <div className="card space-y-3">
            <div className="space-y-1">
              <label className="text-xs font-mono" style={{ color: 'var(--text-muted)' }}>Name</label>
              <input
                value={senderName}
                onChange={e => setSenderName(e.target.value)}
                placeholder="e.g. Ankit Songara"
                className="input text-sm w-full"
              />
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="space-y-1">
                <label className="text-xs font-mono" style={{ color: 'var(--text-muted)' }}>LinkedIn</label>
                <input
                  value={links.linkedin}
                  onChange={e => setLinks(l => ({ ...l, linkedin: e.target.value }))}
                  placeholder="linkedin.com/in/you"
                  className="input text-sm w-full"
                />
              </div>
              <div className="space-y-1">
                <label className="text-xs font-mono" style={{ color: 'var(--text-muted)' }}>GitHub</label>
                <input
                  value={links.github}
                  onChange={e => setLinks(l => ({ ...l, github: e.target.value }))}
                  placeholder="github.com/you"
                  className="input text-sm w-full"
                />
              </div>
              <div className="space-y-1">
                <label className="text-xs font-mono" style={{ color: 'var(--text-muted)' }}>Portfolio</label>
                <input
                  value={links.portfolio}
                  onChange={e => setLinks(l => ({ ...l, portfolio: e.target.value }))}
                  placeholder="yoursite.dev"
                  className="input text-sm w-full"
                />
              </div>
            </div>
            <div className="flex gap-2">
              <button
                onClick={handleSaveName}
                disabled={savingName}
                className="btn text-sm flex items-center gap-1.5"
                style={{
                  background: 'color-mix(in srgb, var(--success) 10%, transparent)',
                  borderColor: 'color-mix(in srgb, var(--success) 25%, transparent)',
                  color: 'var(--success-text)',
                  opacity: savingName ? 0.5 : 1,
                }}
              >
                <Save size={13} />
                {savingName ? 'Saving…' : 'Save signature'}
              </button>
              <button
                // Leaving edit mode lets the populate-effect restore the saved
                // values from the shared config cache — no refetch needed.
                onClick={() => setEditingSig(false)}
                disabled={savingName}
                className="btn text-sm"
                style={{ color: 'var(--text-muted)', borderColor: 'var(--border)' }}
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>

      {/* ── Gmail Credentials (optional) ──────────────────────────────────── */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-xs font-bold font-mono tracking-widest" style={{ color: 'var(--text-muted)' }}>
            GMAIL CREDENTIALS
            <span
              className="ml-2 px-1.5 py-0.5 rounded font-sans font-semibold"
              style={{ background: 'var(--surface-2)', color: 'var(--text-muted)', fontSize: 9, letterSpacing: '0.02em' }}
            >
              OPTIONAL
            </span>
          </span>
          {gmailConnected && (
            <span className="flex items-center gap-1 text-xs" style={{ color: 'var(--success-text)' }}>
              <CheckCircle2 size={12} /> Connected
            </span>
          )}
        </div>

        {gmailConnected && !updatingCreds ? (
          /* ── Connected state (OAuth or App Password) ── */
          <div className="card space-y-3">
            <div
              className="flex items-center gap-2.5 rounded-lg p-3 text-sm"
              style={{ background: 'color-mix(in srgb, var(--success) 8%, transparent)', border: '1px solid color-mix(in srgb, var(--success) 20%, transparent)' }}
            >
              <CheckCircle2 size={16} color="var(--success)" style={{ flexShrink: 0 }} />
              <div>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-medium">Sending as {cfg?.gmail_address || localAddress}</span>
                  {gmailMethod === 'oauth' && (
                    <span
                      className="px-1.5 py-0.5 rounded font-semibold"
                      style={{ background: 'var(--accent-dim)', color: 'var(--accent-text)', fontSize: 10, letterSpacing: '0.02em' }}
                    >
                      Connected with Google
                    </span>
                  )}
                </div>
                {gmailMethod === 'oauth' ? (
                  <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
                    Google may expire this connection weekly while the app is in test
                    mode — reconnect if sending stops.
                  </p>
                ) : (
                  <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
                    Saved securely — Send All, per-contact Send, and reply checks work
                    without re-entering anything, even after a refresh.
                  </p>
                )}
              </div>
            </div>
            <div className="flex gap-2">
              {gmailMethod !== 'oauth' && (
                <button
                  onClick={() => setUpdatingCreds(true)}
                  className="btn text-sm"
                  style={{ color: 'var(--text-muted)', borderColor: 'var(--border)' }}
                >
                  Update credentials
                </button>
              )}
              <button
                onClick={handleDisconnect}
                className="btn text-sm"
                style={{ color: 'var(--danger-text)', borderColor: 'color-mix(in srgb, var(--danger) 30%, transparent)' }}
              >
                Disconnect
              </button>
            </div>
          </div>
        ) : oauthAvailable && !gmailConnected ? (
          /* ── OAuth-first connect (App Password tucked behind an expander) ── */
          <div className="space-y-3">
            <div className="card space-y-3">
              <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                Without Gmail connected you can still hunt contacts, generate emails, and
                send each one from your own Gmail with one click. Connecting enables
                in-app sending ("Send All" and per-contact Send) plus reply tracking.
              </p>
              <button
                onClick={handleOauthStart}
                disabled={startingOauth}
                className="btn text-sm w-full flex items-center justify-center gap-2"
                style={{
                  background: 'var(--accent-dim)',
                  borderColor: 'color-mix(in srgb, var(--accent) 25%, transparent)',
                  color: 'var(--accent-text)',
                  opacity: startingOauth ? 0.5 : 1,
                }}
              >
                <Mail size={14} />
                {startingOauth ? 'Opening Google…' : 'Connect Gmail'}
              </button>
              <p className="text-xs text-center" style={{ color: 'var(--text-muted)' }}>
                One click — sign in with Google, no password to copy.
              </p>
            </div>

            <button
              onClick={() => setAppPwOpen(o => !o)}
              className="flex items-center gap-1 text-xs font-semibold"
              style={{ color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer' }}
            >
              {appPwOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              Use an App Password instead
            </button>
            {appPwOpen && appPasswordForm}
          </div>
        ) : (
          /* ── App Password form — OAuth unavailable, or updating stored creds ── */
          appPasswordForm
        )}
      </div>

      {/* ── Next step ─────────────────────────────────────────────────────── */}
      <div className="flex justify-end pt-2">
        <button
          onClick={() => setActiveTab('hunt')}
          className="btn flex items-center gap-2 text-sm font-semibold"
          style={{
            background: 'color-mix(in srgb, var(--accent) 12%, transparent)',
            borderColor: 'color-mix(in srgb, var(--accent) 35%, transparent)',
            color: 'var(--accent-text)',
            padding: '9px 20px',
          }}
        >
          Next: Hunt Contacts <ArrowRight size={14} />
        </button>
      </div>
    </div>
  )
}
