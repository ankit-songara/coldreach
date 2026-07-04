import { useState, useCallback, useEffect } from 'react'
import { useDropzone } from 'react-dropzone'
import toast from 'react-hot-toast'
import { Eye, EyeOff, CheckCircle2, ExternalLink, ArrowRight, Save } from 'lucide-react'
import { useStore } from '../../store'
import { sendApi } from '../../api/send'
import { resumeApi } from '../../api/resume'
import { automationApi } from '../../api/automation'
import api from '../../api/client'

export default function Setup() {
  const { resume, setResume, gmailAddress, gmailAppPassword, setGmailCreds, setActiveTab } = useStore()
  const [extracting, setExtracting] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const [testing, setTesting] = useState(false)
  const [savingResume, setSavingResume] = useState(false)
  const [localAddress, setLocalAddress]   = useState(gmailAddress)
  const [localPassword, setLocalPassword] = useState(gmailAppPassword)
  const [senderName, setSenderName]           = useState('')
  const [signatureLinks, setSignatureLinks]   = useState('')
  const [savingName, setSavingName]           = useState(false)

  // Load the currently-resolved signature (config override → résumé → email).
  useEffect(() => {
    automationApi.getConfig()
      .then(cfg => {
        setSenderName(cfg.sender_name || '')
        setSignatureLinks(cfg.signature_links || '')
      })
      .catch(() => {})
  }, [])

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
      toast.success(`Extracted ${data.text.length.toLocaleString()} chars from ${file.name}`)
    } catch (e: any) {
      toast.error(e.message)
    } finally {
      setExtracting(false)
    }
  }, [setResume])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'application/pdf': ['.pdf'],
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
    },
    maxFiles: 1,
  })

  const handleSaveCreds = () => {
    setGmailCreds(localAddress.trim(), localPassword.trim())
    toast.success('Gmail credentials saved')
  }

  const handleTestConnection = async () => {
    if (!localAddress || !localPassword) {
      toast.error('Enter your Gmail address and App Password first')
      return
    }
    setTesting(true)
    try {
      const res = await sendApi.test(localAddress.trim(), localPassword.trim())
      setGmailCreds(localAddress.trim(), localPassword.trim())
      toast.success(res.message)
    } catch (e: any) {
      toast.error(e.message)
    } finally {
      setTesting(false)
    }
  }

  const handleSaveName = async () => {
    setSavingName(true)
    try {
      const cfg = await automationApi.setProfile(senderName.trim(), signatureLinks.trim())
      setSenderName(cfg.sender_name || '')
      setSignatureLinks(cfg.signature_links || '')
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
      toast.success('Resume saved')
    } catch (e: any) {
      toast.error(e.message)
    } finally {
      setSavingResume(false)
    }
  }

  const credsMatch = localAddress === gmailAddress && localPassword === gmailAppPassword
  const credsSaved = !!gmailAddress && !!gmailAppPassword && credsMatch

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold tracking-wide mb-1" style={{ fontFamily: 'var(--font-display)' }}>Setup</h1>
        <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
          Configure your resume and Gmail once. ColdReach handles the rest.
        </p>
      </div>

      {/* ── Gmail Credentials ─────────────────────────────────────────────── */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-xs font-bold font-mono tracking-widest" style={{ color: 'var(--text-dim)' }}>
            GMAIL CREDENTIALS
          </span>
          {credsSaved && (
            <span className="flex items-center gap-1 text-xs" style={{ color: '#3f8f43' }}>
              <CheckCircle2 size={12} /> Connected
            </span>
          )}
        </div>

        <div className="card space-y-3">
          <div className="space-y-1">
            <label className="text-xs font-mono" style={{ color: 'var(--text-dim)' }}>Gmail address</label>
            <input
              type="email"
              value={localAddress}
              onChange={e => setLocalAddress(e.target.value)}
              placeholder="you@gmail.com"
              className="input text-sm w-full"
            />
          </div>

          <div className="space-y-1">
            <label className="text-xs font-mono" style={{ color: 'var(--text-dim)' }}>App Password</label>
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
            style={{ background: 'rgba(226,96,63,0.05)', border: '1px solid rgba(226,96,63,0.12)', color: 'var(--text-muted)' }}>
            <p className="font-semibold" style={{ color: 'var(--text)' }}>How to get an App Password:</p>
            <p>1. Enable 2-Step Verification on your Google account</p>
            <p>2. Go to <span style={{ color: 'var(--accent)' }}>myaccount.google.com/apppasswords</span></p>
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
            <p className="pt-1" style={{ color: 'var(--text-dim)' }}>
              🔒 Kept only for this session — never saved to this browser or our servers,
              so you'll re-enter it after a page refresh.
            </p>
          </div>

          <div className="flex gap-2">
            <button
              onClick={handleTestConnection}
              disabled={testing || !localAddress || !localPassword}
              className="btn text-sm flex-1"
              style={{
                background: 'rgba(226,96,63,0.10)',
                borderColor: 'rgba(226,96,63,0.25)',
                color: 'var(--accent)',
                opacity: testing || !localAddress || !localPassword ? 0.5 : 1,
              }}
            >
              {testing ? 'Testing…' : 'Test & Save'}
            </button>
            {!credsMatch && localAddress && localPassword && (
              <button
                onClick={handleSaveCreds}
                className="btn text-sm"
                style={{ color: 'var(--text-muted)', borderColor: 'var(--border)' }}
              >
                Save only
              </button>
            )}
          </div>
        </div>
      </div>

      {/* ── Signature ─────────────────────────────────────────────────────── */}
      <div className="space-y-3">
        <span className="text-xs font-bold font-mono tracking-widest" style={{ color: 'var(--text-dim)' }}>
          SIGNATURE
        </span>
        <div className="card space-y-3">
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
            Signed at the bottom of every email (“Best regards, …”). The name is auto-detected
            from your résumé — override it if it’s wrong. Links render as one line under your
            name; for a job-seeking email this is the click-through that gets you looked up.
          </p>
          <div className="space-y-1">
            <label className="text-xs font-mono" style={{ color: 'var(--text-dim)' }}>Name</label>
            <input
              value={senderName}
              onChange={e => setSenderName(e.target.value)}
              placeholder="e.g. Ankit Songara"
              className="input text-sm w-full"
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-mono" style={{ color: 'var(--text-dim)' }}>
              Links (optional — GitHub / LinkedIn / portfolio)
            </label>
            <input
              value={signatureLinks}
              onChange={e => setSignatureLinks(e.target.value)}
              placeholder="github.com/you · linkedin.com/in/you"
              className="input text-sm w-full"
            />
          </div>
          <button
            onClick={handleSaveName}
            disabled={savingName}
            className="btn text-sm flex items-center gap-1.5"
            style={{
              background: 'rgba(63,143,67,0.10)',
              borderColor: 'rgba(63,143,67,0.25)',
              color: '#3f8f43',
              opacity: savingName ? 0.5 : 1,
            }}
          >
            <Save size={13} />
            {savingName ? 'Saving…' : 'Save signature'}
          </button>
        </div>
      </div>

      {/* ── Resume ────────────────────────────────────────────────────────── */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-xs font-bold font-mono tracking-widest" style={{ color: 'var(--text-dim)' }}>
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
          <p className="text-xs mt-1" style={{ color: 'var(--text-dim)' }}>Max 15 MB</p>
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
              background: 'rgba(63,143,67,0.10)',
              borderColor: 'rgba(63,143,67,0.25)',
              color: '#3f8f43',
              opacity: savingResume || !resume.trim() ? 0.5 : 1,
            }}
          >
            <Save size={13} />
            {savingResume ? 'Saving…' : 'Save Resume'}
          </button>
        </div>
      </div>

      {/* ── Next step ─────────────────────────────────────────────────────── */}
      <div className="flex justify-end pt-2">
        <button
          onClick={() => setActiveTab('hunt')}
          className="btn flex items-center gap-2 text-sm font-semibold"
          style={{
            background: 'rgba(226,96,63,0.12)',
            borderColor: 'rgba(226,96,63,0.35)',
            color: 'var(--accent)',
            padding: '9px 20px',
          }}
        >
          Next: Hunt Contacts <ArrowRight size={14} />
        </button>
      </div>
    </div>
  )
}
