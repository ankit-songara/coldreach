import { useEffect, useState } from 'react'
import {
  TrendingUp, Activity, MailOpen, CalendarCheck, Clock, Wand2,
  Send as SendIcon, MessageCircle, BarChart2, Lightbulb,
  Search, Settings, AlertTriangle, CheckCircle2, Circle, ArrowRight,
  Trophy, Timer,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import toast from 'react-hot-toast'
import { useQuery } from '@tanstack/react-query'
import { useStore } from '../../store'
import api from '../../api/client'
import { demoApi, DEMO_SENTINEL } from '../../api/demo'
import { contactsApi } from '../../api/contacts'
import { resumeApi } from '../../api/resume'
import { useContacts } from '../../hooks/useContacts'
import { useAllDrafts } from '../../hooks/useAllDrafts'
import { useAutomationConfig } from '../../hooks/useAutomationConfig'

function greeting() {
  const h = new Date().getHours()
  if (h < 12) return 'Good morning'
  if (h < 17) return 'Good afternoon'
  return 'Good evening'
}
function firstName(email: string) {
  // "priya.n@…" → "Priya" (digits and separators are noise). Long mashed-up
  // locals ("ankitsongara2003" → "Ankitsongara") read like a bot wrote them —
  // greet generically instead; the résumé-detected name takes over once known.
  const raw = (email || '').split('@')[0].split(/[._\-]/)[0].replace(/\d+/g, '')
  if (!raw || raw.length > 10) return 'there'
  return raw[0].toUpperCase() + raw.slice(1)
}

// Show at most `max` names, then "+ N more" — an alert card must never grow
// into a wall of 183 comma-separated names.
function nameList(items: { name: string }[], max = 3): string {
  const names = items.slice(0, max).map(i => i.name)
  const more = items.length - names.length
  return names.join(', ') + (more > 0 ? ` + ${more} more` : '')
}
function fmtDate(d: Date) {
  return d.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })
}

// ── LLM health banner ───────────────────────────────────────────────────────
// The raw provider label ("unavailable: No LLM provider…") is operator-speak —
// it goes in a hover tooltip for whoever runs the server, never in the copy.
function LLMBanner({ label }: { label: string }) {
  return (
    <div
      className="flex items-center gap-3 rounded-xl px-4 py-3"
      style={{ background: 'rgba(196,125,30,0.09)', border: '1px solid rgba(196,125,30,0.28)' }}
      title={label}
    >
      <AlertTriangle size={16} color="#c47d1e" style={{ flexShrink: 0 }} />
      <div className="flex-1 text-sm" style={{ color: '#c47d1e' }}>
        <strong>Email writing is temporarily unavailable.</strong>{' '}
        <span style={{ color: 'var(--text-muted)' }}>
          You can still hunt contacts and track replies — try generating drafts again in a few minutes.
        </span>
      </div>
    </div>
  )
}

// ── Onboarding flow (shown to brand-new users) ───────────────────────────────
function OnboardingFlow({ resume, gmailConnected, contacts, onTab, onSeedDemo, seeding, onSkipGmail }: {
  resume: string
  gmailConnected: boolean
  contacts: number
  onTab: (t: 'setup' | 'hunt' | 'compose' | 'send') => void
  onSeedDemo: () => void
  seeding: boolean
  onSkipGmail: () => void
}) {
  const steps = [
    {
      n: 1, done: resume.trim().length > 0,
      title: 'Upload your resume',
      body: 'ColdReach personalises every email using your actual experience. PDF or DOCX, under 15 MB.',
      cta: 'Go to Setup', tab: 'setup' as const, color: '#e2603f', bg: 'var(--accent-tint)',
    },
    {
      n: 2, done: gmailConnected,
      title: 'Connect Gmail (optional)',
      body: 'Lets ColdReach send for you and track replies automatically. Skip it — you can always send each email from your own Gmail with one click.',
      cta: 'Connect Gmail', tab: 'setup' as const, color: '#c47d1e', bg: 'rgba(196,125,30,.10)',
    },
    {
      n: 3, done: contacts > 0,
      title: 'Find contacts',
      body: 'Type a role or company — ColdReach finds hiring managers, recruiters, and founders who are actively hiring, with a real email for each.',
      cta: 'Hunt contacts', tab: 'hunt' as const, color: '#6f5ae0', bg: 'rgba(111,90,224,.10)',
    },
    {
      n: 4, done: false,
      title: 'Generate personalised emails',
      body: 'One click generates a targeted cold email for every contact using your resume + their context.',
      cta: 'Compose', tab: 'compose' as const, color: '#0e9d88', bg: 'rgba(14,157,136,.10)',
    },
    {
      n: 5, done: false,
      title: 'Send & track',
      body: 'Send via Gmail SMTP, check replies in one click, and track every outcome from first touch to offer.',
      cta: 'Send', tab: 'send' as const, color: '#3f8f43', bg: 'rgba(63,143,67,.10)',
    },
  ]

  const firstPending = steps.find(s => !s.done)

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 22, fontWeight: 800, letterSpacing: '-0.02em', color: 'var(--text)', margin: '0 0 6px' }}>
            5 steps to your first reply
          </h2>
          <p className="text-[14px]" style={{ color: 'var(--text-muted)', margin: 0 }}>
            Complete these once — then ColdReach runs on autopilot.
          </p>
        </div>
        <button
          onClick={onSeedDemo}
          disabled={seeding}
          className="flex items-center gap-2 text-sm font-semibold flex-shrink-0"
          style={{ padding: '9px 16px', borderRadius: 'var(--radius-full)', background: 'var(--surface-1)', border: '1px solid var(--border-strong)', color: 'var(--text)', cursor: 'pointer', boxShadow: 'var(--shadow-xs)' }}
        >
          {seeding ? 'Loading…' : '👀 Explore with sample data'}
        </button>
      </div>

      <div className="space-y-3">
        {steps.map(step => {
          const isActive = firstPending?.n === step.n
          return (
            <div
              key={step.n}
              onClick={() => !step.done && onTab(step.tab)}
              className="flex items-start gap-4"
              style={{
                background: 'var(--surface-1)',
                border: `1px solid ${isActive ? step.color + '45' : step.done ? 'var(--border-dim)' : 'var(--border)'}`,
                borderRadius: 16,
                padding: '18px 20px',
                cursor: step.done ? 'default' : 'pointer',
                opacity: step.done ? 0.55 : 1,
                boxShadow: isActive ? `0 0 0 2px ${step.color}18, var(--shadow-sm)` : 'var(--shadow-xs)',
                transition: 'all 160ms',
              }}
            >
              {/* Step indicator */}
              <div style={{ flexShrink: 0, marginTop: 2 }}>
                {step.done
                  ? <CheckCircle2 size={22} color="#3f8f43" />
                  : isActive
                    ? <div style={{ width: 22, height: 22, borderRadius: '50%', background: step.color, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                        <span style={{ color: '#fff', fontSize: 12, fontWeight: 800 }}>{step.n}</span>
                      </div>
                    : <Circle size={22} style={{ color: 'var(--border-strong)' }} />
                }
              </div>

              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-3">
                  <span
                    className="font-bold text-[15px]"
                    style={{ color: step.done ? 'var(--text-dim)' : 'var(--text)', textDecoration: step.done ? 'line-through' : 'none' }}
                  >
                    {step.title}
                  </span>
                  {!step.done && (
                    <span className="flex items-center gap-2 flex-shrink-0">
                      {/* Optional step: let the user dismiss it instead of the
                          active-step ring nagging on it forever. */}
                      {step.n === 2 && (
                        <button
                          onClick={e => { e.stopPropagation(); onSkipGmail() }}
                          className="text-xs font-semibold"
                          style={{ color: 'var(--text-dim)', background: 'none', border: 'none', cursor: 'pointer' }}
                        >
                          Skip for now
                        </button>
                      )}
                      <button
                        onClick={e => { e.stopPropagation(); onTab(step.tab) }}
                        className="flex items-center gap-1.5 text-xs font-semibold"
                        style={{ color: step.color, background: step.bg, border: `1px solid ${step.color}35`, borderRadius: 'var(--radius-full)', padding: '5px 12px', cursor: 'pointer' }}
                      >
                        {step.cta} <ArrowRight size={11} />
                      </button>
                    </span>
                  )}
                </div>
                {!step.done && (
                  <p className="text-[13px] mt-1.5" style={{ color: 'var(--text-muted)', lineHeight: 1.55, margin: '6px 0 0' }}>
                    {step.body}
                  </p>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {/* Starter queries */}
      <div
        style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 14, padding: '16px 20px' }}
      >
        <p className="text-xs font-bold font-mono tracking-widest mb-3" style={{ color: 'var(--text-dim)' }}>
          TRY THESE HUNT QUERIES
        </p>
        <div className="flex flex-wrap gap-2">
          {[
            'software engineer hiring india',
            'react developer hiring',
            'python backend hiring',
            'founding engineer',
            'fullstack engineer remote',
            'golang hiring',
            'data engineer hiring',
            'devops sre hiring',
          ].map(q => (
            <button
              key={q}
              onClick={() => onTab('hunt')}
              className="text-xs px-3 py-1.5 rounded-full font-mono border transition-colors"
              style={{ borderColor: 'var(--border-strong)', color: 'var(--text-muted)', background: 'var(--surface-1)' }}
            >
              {q}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── Animated funnel bar ─────────────────────────────────────────────────────
function FunnelRow({ label, count, total, color, delay, onClick }: {
  label: string; count: number; total: number; color: string; delay: number; onClick?: () => void
}) {
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const t = setTimeout(() => setWidth(total > 0 ? (count / total) * 100 : 0), delay)
    return () => clearTimeout(t)
  }, [count, total, delay])
  const pct = total > 0 ? Math.round((count / total) * 100) : 0

  return (
    <div
      onClick={onClick}
      className="flex items-center gap-3.5"
      style={{ padding: '11px 0', borderBottom: '1px solid var(--border)', cursor: onClick ? 'pointer' : 'default' }}
    >
      <div style={{ width: 120, flexShrink: 0 }}>
        <span className="text-[13px] font-semibold" style={{ color: 'var(--text)' }}>{label}</span>
      </div>
      <div className="flex-1" style={{ height: 10, borderRadius: 99, background: 'var(--surface-2)', overflow: 'hidden' }}>
        <div style={{ height: '100%', borderRadius: 99, background: color, width: `${width}%`, transition: 'width 0.7s var(--ease-out)', minWidth: count > 0 ? 10 : 0 }} />
      </div>
      <div className="flex items-center gap-2 flex-shrink-0 justify-end" style={{ minWidth: 76 }}>
        <span className="font-bold" style={{ fontSize: 18, color }}>{count}</span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-dim)' }}>{pct}%</span>
      </div>
    </div>
  )
}

// ── Alert card ──────────────────────────────────────────────────────────────
function AlertCard({ icon: Icon, color, bg, title, body, action, onAction }: {
  icon: LucideIcon; color: string; bg: string; title: string; body: string; action: string; onAction: () => void
}) {
  const [hover, setHover] = useState(false)
  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onClick={onAction}
      className="flex items-start gap-3.5"
      style={{
        background: 'var(--surface-1)', border: `1px solid ${hover ? color + '50' : 'var(--border)'}`,
        borderRadius: 14, padding: '16px 18px', cursor: 'pointer',
        boxShadow: hover ? 'var(--shadow-md)' : 'var(--shadow-sm)',
        transform: hover ? 'translateY(-1px)' : 'none', transition: 'all 180ms',
      }}
    >
      <div className="flex items-center justify-center flex-shrink-0" style={{ width: 36, height: 36, borderRadius: 10, background: bg }}>
        <Icon size={17} color={color} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="font-bold text-sm" style={{ color: 'var(--text)', marginBottom: 2 }}>{title}</div>
        {/* Clamped: an alert is a headline, not a report — long bodies get cut */}
        <div
          className="text-[13px]"
          title={body}
          style={{
            color: 'var(--text-muted)', lineHeight: 1.5,
            display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden',
          }}
        >
          {body}
        </div>
      </div>
      <span className="text-xs font-semibold flex-shrink-0" style={{ color, marginTop: 2 }}>{action} →</span>
    </div>
  )
}

// ── Stat tile ───────────────────────────────────────────────────────────────
function StatTile({ label, value, sub, color, icon: Icon, iconBg }: {
  label: string; value: number; sub?: string; color: string; icon: LucideIcon; iconBg: string
}) {
  return (
    <div className="flex items-center gap-3.5" style={{ background: 'var(--surface-1)', border: '1px solid var(--border)', borderRadius: 16, padding: '18px 20px', boxShadow: 'var(--shadow-sm)' }}>
      <div className="flex items-center justify-center flex-shrink-0" style={{ width: 44, height: 44, borderRadius: 14, background: iconBg }}>
        <Icon size={22} color={color} />
      </div>
      <div>
        <div style={{ fontFamily: 'var(--font-display)', fontSize: 28, fontWeight: 800, color, lineHeight: 1.1 }}>{value}</div>
        <div className="text-[13px] font-medium" style={{ color: 'var(--text-muted)', marginTop: 2 }}>{label}</div>
        {sub && <div className="text-xs" style={{ color: 'var(--text-dim)', marginTop: 1 }}>{sub}</div>}
      </div>
    </div>
  )
}

const llmOk = (h?: { llm_ok?: boolean; llm?: string }) =>
  // Prefer the explicit flag; fall back to label sniffing for older backends.
  h ? (h.llm_ok ?? !(h.llm || '').includes('unavailable')) : undefined

export default function Today() {
  const { contacts, drafts, userEmail, resume, gmailAddress, setActiveTab, setContacts, setResume } = useStore()
  const [seeding, setSeeding] = useState(false)
  const [gmailSkipped, setGmailSkipped] = useState(
    () => localStorage.getItem('coldreach-gmail-skipped') === '1'
  )

  // Contacts + drafts come from the shared queries (also used by App, Hunt,
  // Compose, Send) — one fetch each per load instead of one per tab.
  const { contactsLoaded } = useContacts()
  useAllDrafts()

  // Server-stored config: Gmail connection + sender name for greeting (shared
  // query — Setup and Send read the same cache).
  const { data: cfg } = useAutomationConfig()
  const gmailLinked = cfg?.has_gmail ?? false
  const senderName  = cfg?.sender_name ?? ''

  // LLM health. While it's down, re-check every 60s so the banner clears itself
  // when the provider recovers instead of sticking until a hard refresh.
  const { data: health, isError: healthUnreachable } = useQuery({
    queryKey: ['health'],
    queryFn: () => api.get<{ llm_ok?: boolean; llm: string }>('/health').then(r => r.data),
    refetchInterval: q => (llmOk(q.state.data) === true ? false : 60_000),
    refetchOnWindowFocus: true,
  })
  const llmReady = healthUnreachable ? false : (llmOk(health) ?? null)
  const llmLabel = healthUnreachable ? 'unreachable' : (health?.llm ?? '')

  const refreshAll = async () => {
    try { setContacts(await contactsApi.list()) } catch { /* ignore */ }
    try { const r = await resumeApi.getLatest(); setResume(r.text || '') } catch { /* ignore */ }
  }

  const loadDemo = async () => {
    setSeeding(true)
    try {
      await demoApi.seed()
      await refreshAll()
      toast.success('Sample data loaded — explore the funnel & sources')
    } catch (e: any) {
      toast.error(e.message ?? 'Could not load sample data')
    } finally {
      setSeeding(false)
    }
  }

  const clearDemo = async () => {
    try {
      await demoApi.clear()
      await refreshAll()
      toast('Sample data cleared', { icon: '🧹' })
    } catch (e: any) {
      toast.error(e.message ?? 'Could not clear sample data')
    }
  }

  const isDemo = contacts.some(c => (c.notes ?? '') === DEMO_SENTINEL)

  const hasDraftFor = (id: number) => (drafts[id] ?? []).some(d => !d.is_followup)

  // A contact that has been emailed (first-touch delivered), in any later state.
  const SENT_STATUSES = ['emailed', 'followed_up', 'replied', 'interview', 'offer', 'rejected']

  const total     = contacts.length
  const verified  = contacts.filter(c => c.email_status === 'valid' || c.email_status === 'risky').length
  const hasDraft  = contacts.filter(c => hasDraftFor(c.id)).length
  const sent      = contacts.filter(c => SENT_STATUSES.includes(c.status)).length
  // Funnel buckets are cumulative: an offer implies they interviewed + replied.
  const replied   = contacts.filter(c => ['replied', 'interview', 'offer'].includes(c.status)).length
  const interview = contacts.filter(c => ['interview', 'offer'].includes(c.status)).length
  const offer     = contacts.filter(c => c.status === 'offer').length

  const replyRate     = sent > 0      ? Math.round((replied / sent) * 100)       : 0
  const interviewRate = replied > 0   ? Math.round((interview / replied) * 100)  : 0
  const offerRate     = interview > 0 ? Math.round((offer / interview) * 100)    : 0

  // Avg days to first reply — proxy: time from last send to the reply timestamp.
  const replyGaps = contacts
    .filter(c => c.replied_at && c.last_emailed_at)
    .map(c => (new Date(c.replied_at as string).getTime() - new Date(c.last_emailed_at as string).getTime()) / 86_400_000)
    .filter(d => d >= 0 && d < 120)
  const avgReplyDays = replyGaps.length
    ? Math.round((replyGaps.reduce((a, b) => a + b, 0) / replyGaps.length) * 10) / 10
    : null

  const activeInterviews = contacts.filter(c => c.status === 'interview')
  const offersWon        = contacts.filter(c => c.status === 'offer')
  const followupsDue  = contacts.filter(c => c.status === 'emailed').length
  const recentReplies = contacts.filter(c => c.status === 'replied')
  const ungenerated   = contacts.filter(
    c => !SENT_STATUSES.includes(c.status) && !hasDraftFor(c.id)
  )

  type Alert = { id: string; icon: LucideIcon; color: string; bg: string; title: string; body: string; action: string; tab: 'today' | 'setup' | 'hunt' | 'compose' | 'send' }
  const alerts: Alert[] = []
  if (recentReplies.length > 0) {
    alerts.push({
      id: 'reply', icon: MailOpen, color: '#0e9d88', bg: 'rgba(14,157,136,.13)',
      title: `${recentReplies.length} new ${recentReplies.length === 1 ? 'reply' : 'replies'}`,
      body: nameList(recentReplies) + (recentReplies.length === 1 ? ' replied — follow up or set up a call.' : ' replied — time to respond!'),
      action: 'View in Send', tab: 'send',
    })
  }
  if (offersWon.length > 0) {
    alerts.push({
      id: 'offer', icon: Trophy, color: '#2f9e44', bg: 'rgba(47,158,68,.14)',
      title: `🎉 ${offersWon.length} offer${offersWon.length > 1 ? 's' : ''}!`,
      body: offersWon.slice(0, 3).map(c => `${c.name} at ${c.company}`).join(' · ')
        + (offersWon.length > 3 ? ` + ${offersWon.length - 3} more` : '') + ' — congratulations.',
      action: 'View', tab: 'send',
    })
  }
  if (activeInterviews.length > 0) {
    alerts.push({
      id: 'interview', icon: CalendarCheck, color: '#3f8f43', bg: 'rgba(63,143,67,.13)',
      title: `${activeInterviews.length} interview${activeInterviews.length > 1 ? 's' : ''} lined up`,
      body: activeInterviews.slice(0, 3).map(c => `${c.name} at ${c.company}`).join(' · ')
        + (activeInterviews.length > 3 ? ` + ${activeInterviews.length - 3} more` : ''),
      action: 'Track', tab: 'send',
    })
  }
  if (followupsDue > 0) {
    alerts.push({
      id: 'followup', icon: Clock, color: '#c47d1e', bg: 'rgba(196,125,30,.13)',
      title: `${followupsDue} contact${followupsDue > 1 ? 's' : ''} waiting on a follow-up`,
      body: `They haven't replied yet — a friendly nudge 3–5 days after the first email gets ~40% more replies. Write one in Compose, then send it from the Send tab.`,
      action: 'Write follow-up', tab: 'compose',
    })
  }
  if (ungenerated.length > 0) {
    alerts.push({
      id: 'draft', icon: Wand2, color: '#e2603f', bg: 'rgba(226,96,63,.10)',
      title: `${ungenerated.length} contact${ungenerated.length > 1 ? 's' : ''} need${ungenerated.length === 1 ? 's' : ''} a draft`,
      body: nameList(ungenerated) + ' — one click to generate personalised emails.',
      action: 'Compose', tab: 'compose',
    })
  }

  const name = senderName ? senderName.split(' ')[0] : firstName(userEmail)
  // Brand-new user: no contacts and no resume uploaded yet
  const isNewUser = total === 0 && !resume.trim()

  // Until the first contacts fetch resolves we don't know whether this is a new
  // user or a returning one — render a quiet skeleton instead of flashing the
  // onboarding flow (or a zeroed funnel) and then swapping it out.
  if (!contactsLoaded && total === 0) {
    return (
      <div className="flex flex-col animate-pulse" style={{ gap: 28 }} aria-hidden>
        <div>
          <div className="h-8 rounded w-64 mb-3" style={{ background: 'var(--surface-3)' }} />
          <div className="h-4 rounded w-80" style={{ background: 'var(--surface-2)' }} />
        </div>
        <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))', gap: 12 }}>
          {[0, 1, 2, 3].map(i => (
            <div key={i} className="h-20 rounded-2xl" style={{ background: 'var(--surface-2)' }} />
          ))}
        </div>
        <div className="h-64 rounded-2xl" style={{ background: 'var(--surface-2)' }} />
      </div>
    )
  }

  return (
    <div className="flex flex-col" style={{ gap: 28 }}>
      {/* ── LLM health banner (shown only when LLM is unavailable) ── */}
      {llmReady === false && <LLMBanner label={llmLabel} />}

      {/* ── Sample-data banner ── */}
      {isDemo && (
        <div className="flex items-center gap-3 rounded-xl px-4 py-2.5" style={{ background: 'var(--surface-2)', border: '1px dashed var(--border-strong)' }}>
          <span className="text-sm" style={{ color: 'var(--text-muted)' }}>
            👀 You're viewing <strong style={{ color: 'var(--text)' }}>sample data</strong> — explore freely, then clear it before your real search.
          </span>
          <button onClick={clearDemo} className="text-xs font-semibold ml-auto flex-shrink-0" style={{ color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer' }}>
            Clear sample data
          </button>
        </div>
      )}

      {/* ── Greeting ── */}
      <div className="flex items-end justify-between flex-wrap" style={{ gap: 12 }}>
        <div>
          <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 32, fontWeight: 800, letterSpacing: '-0.025em', margin: '0 0 4px', color: 'var(--text)' }}>
            {greeting()}, {name}.
          </h1>
          <p className="text-[15px]" style={{ color: 'var(--text-muted)', margin: 0 }}>
            {fmtDate(new Date())} · {replyRate > 0
              ? `Your reply rate is ${replyRate}% this week`
              : 'Your next opportunity is one email away — let’s go find it.'}
          </p>
        </div>
        <div
          className="flex items-center gap-2"
          style={{ padding: '8px 16px', borderRadius: 'var(--radius-full)', background: replyRate >= 20 ? 'rgba(63,143,67,.10)' : 'var(--surface-2)', border: `1px solid ${replyRate >= 20 ? 'rgba(63,143,67,.25)' : 'var(--border)'}` }}
        >
          {replyRate >= 20
            ? <TrendingUp size={14} color="#3f8f43" />
            : <Activity size={14} style={{ color: 'var(--text-muted)' }} />}
          <span className="text-[13px] font-semibold" style={{ color: replyRate >= 20 ? '#3f8f43' : 'var(--text-muted)' }}>
            {replyRate}% reply rate
          </span>
        </div>
      </div>

      {/* ── New-user onboarding OR normal dashboard ── */}
      {isNewUser ? (
        <OnboardingFlow
          resume={resume}
          gmailConnected={gmailLinked || !!gmailAddress || gmailSkipped}
          contacts={total}
          onTab={(t) => setActiveTab(t)}
          onSeedDemo={loadDemo}
          seeding={seeding}
          onSkipGmail={() => {
            localStorage.setItem('coldreach-gmail-skipped', '1')
            setGmailSkipped(true)
          }}
        />
      ) : (
        <>
          {/* ── Alerts ── */}
          {alerts.length > 0 && (
            <div className="grid" style={{ gap: 10, gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))' }}>
              {alerts.map(a => (
                <AlertCard key={a.id} icon={a.icon} color={a.color} bg={a.bg} title={a.title} body={a.body} action={a.action} onAction={() => setActiveTab(a.tab)} />
              ))}
            </div>
          )}

          {/* ── Stats row ── */}
          <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))', gap: 12 }}>
            <StatTile label="Emails sent" value={sent}      icon={SendIcon}      color="var(--accent)" iconBg="var(--accent-tint)" />
            <StatTile label="Replied"     value={replied}   icon={MessageCircle} color="#0e9d88"       iconBg="rgba(14,157,136,.12)" sub={sent > 0 ? `${replyRate}% of sent` : undefined} />
            <StatTile label="Interviews"  value={interview} icon={CalendarCheck} color="#3f8f43"       iconBg="rgba(63,143,67,.12)"  sub={replied > 0 ? `${interviewRate}% of replies` : undefined} />
            <StatTile label="Offers"      value={offer}     icon={Trophy}        color="#2f9e44"       iconBg="rgba(47,158,68,.12)"  sub={interview > 0 ? `${offerRate}% of interviews` : undefined} />
            <StatTile label="Avg reply"   value={avgReplyDays ?? 0} icon={Timer} color="#c47d1e"       iconBg="rgba(196,125,30,.12)" sub={avgReplyDays != null ? 'days to first reply' : 'no replies yet'} />
          </div>

          {/* ── Conversion funnel ── */}
          <div style={{ background: 'var(--surface-1)', border: '1px solid var(--border)', borderRadius: 18, padding: '24px 28px', boxShadow: 'var(--shadow-sm)' }}>
            <div className="flex items-center justify-between" style={{ marginBottom: 20 }}>
              <div>
                <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 18, fontWeight: 700, margin: '0 0 3px', letterSpacing: '-0.015em', color: 'var(--text)' }}>Outreach pipeline</h2>
                <p className="text-[13px]" style={{ color: 'var(--text-muted)', margin: 0 }}>Conversion at each stage of your funnel</p>
              </div>
              <div className="flex items-center gap-1.5" style={{ padding: '5px 12px', borderRadius: 'var(--radius-full)', background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
                <BarChart2 size={13} style={{ color: 'var(--text-muted)' }} />
                <span className="text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>Pipeline</span>
              </div>
            </div>

            <FunnelRow label="Hunted"    count={total}     total={total}                 color="#e2603f" delay={100} onClick={() => setActiveTab('hunt')} />
            <FunnelRow label="Verified"  count={verified}  total={total}                 color="#c47d1e" delay={200} />
            <FunnelRow label="Drafted"   count={hasDraft}  total={total}                 color="#6f5ae0" delay={300} onClick={() => setActiveTab('compose')} />
            <FunnelRow label="Sent"      count={sent}      total={total}                 color="#0e9d88" delay={400} onClick={() => setActiveTab('send')} />
            <FunnelRow label="Replied"   count={replied}   total={Math.max(sent, 1)}     color="#3f8f43" delay={500} onClick={() => setActiveTab('send')} />
            <FunnelRow label="Interview" count={interview} total={Math.max(replied, 1)}  color="#c47d1e" delay={600} onClick={() => setActiveTab('send')} />
            <FunnelRow label="Offer"     count={offer}     total={Math.max(interview, 1)} color="#2f9e44" delay={700} onClick={() => setActiveTab('send')} />

            {sent > 0 && (
              <div className="flex items-start gap-2.5" style={{ marginTop: 18, padding: '12px 16px', borderRadius: 12, background: 'var(--surface-2)' }}>
                <Lightbulb size={14} color="#c47d1e" style={{ flexShrink: 0, marginTop: 2 }} />
                <span className="text-[13px]" style={{ color: 'var(--text-muted)' }}>
                  {replyRate >= 20 ? `Strong ${replyRate}% reply rate — above the ~15% average for personalised cold email.` :
                   replyRate >= 10 ? `${replyRate}% reply rate. Try more specific subject lines or adding a company reference.` :
                   `Low reply rate so far. Try shorter emails (< 5 sentences) and more personalised openers.`}
                  {avgReplyDays != null && ` Replies land in ~${avgReplyDays} days on average — nudge non-responders after 3–5.`}
                </span>
              </div>
            )}
          </div>

          {/* ── Quick actions ── */}
          <div>
            <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 16, fontWeight: 700, letterSpacing: '-0.01em', color: 'var(--text)', margin: '0 0 12px' }}>Quick actions</h2>
            <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 10 }}>
              {([
                { icon: Search,   label: 'Find more contacts', sub: 'Fresh leads for your pipeline', tab: 'hunt' as const, color: 'var(--accent)', bg: 'var(--accent-tint)' },
                { icon: Wand2,    label: 'Generate drafts',    sub: `${ungenerated.length} contacts waiting`,  tab: 'compose' as const, color: '#6f5ae0', bg: 'rgba(111,90,224,.10)' },
                { icon: SendIcon, label: 'Send emails',        sub: `${hasDraft} ready to go`,                tab: 'send' as const,    color: '#0e9d88', bg: 'rgba(14,157,136,.10)' },
                { icon: Settings, label: 'Setup Gmail',        sub: 'Connect your account',                   tab: 'setup' as const,   color: '#c47d1e', bg: 'rgba(196,125,30,.10)' },
              ]).map(a => {
                const Icon = a.icon
                return (
                  <button
                    key={a.tab}
                    onClick={() => setActiveTab(a.tab)}
                    className="flex items-center gap-3 text-left transition-all"
                    style={{ padding: '14px 16px', background: 'var(--surface-1)', border: '1px solid var(--border)', borderRadius: 14, cursor: 'pointer', boxShadow: 'var(--shadow-xs)' }}
                    onMouseEnter={e => { e.currentTarget.style.boxShadow = 'var(--shadow-md)'; e.currentTarget.style.transform = 'translateY(-1px)'; e.currentTarget.style.borderColor = a.color + '50' }}
                    onMouseLeave={e => { e.currentTarget.style.boxShadow = 'var(--shadow-xs)'; e.currentTarget.style.transform = 'none'; e.currentTarget.style.borderColor = 'var(--border)' }}
                  >
                    <div className="flex items-center justify-center flex-shrink-0" style={{ width: 36, height: 36, borderRadius: 10, background: a.bg }}>
                      <Icon size={17} color={a.color} />
                    </div>
                    <div>
                      <div className="font-semibold text-sm" style={{ color: 'var(--text)' }}>{a.label}</div>
                      <div className="text-xs" style={{ color: 'var(--text-muted)', marginTop: 1 }}>{a.sub}</div>
                    </div>
                  </button>
                )
              })}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
