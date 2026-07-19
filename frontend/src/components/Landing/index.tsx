import { useEffect, useState } from 'react'
import { Sun, Moon, Monitor } from 'lucide-react'
import Logo from '../shared/Logo'
import { cycleTheme, getStoredTheme, type Theme } from '../../lib/theme'
import './landing.css'

// Hero pipeline bar targets (user-approved): Sent 100%, Replied 23%,
// Interview 8%, Offer 3%. They start at 0 and transition in on mount.
const BARS = [
  { key: 'sent', label: 'Sent', width: '100%', count: 80 },
  { key: 'replied', label: 'Replied', width: '23%', count: 18 },
  { key: 'interview', label: 'Interview', width: '8%', count: 6 },
  { key: 'offer', label: 'Offer', width: '3%', count: 2 },
] as const

const STEPS = [
  {
    n: 1,
    title: 'Drop in your résumé',
    body: "PDF or DOCX. ColdReach learns your skills, projects, and voice — that's all the setup there is.",
  },
  {
    n: 2,
    title: 'Hunt live openings',
    body: 'Type a role or company. We surface founders, hiring managers, and recruiters actively hiring — each with a verified email.',
  },
  {
    n: 3,
    title: 'Personalise in one click',
    body: 'Every draft is grounded in your real experience and their real context. Edit anything before it goes out.',
  },
  {
    n: 4,
    title: 'Send & track replies',
    body: 'Paced sending from your Gmail keeps you reputation-safe. Replies are detected automatically and land in your pipeline.',
  },
]

const FEATURES = [
  {
    icon: '✓',
    title: 'Verified before you send',
    body: 'Every address is checked for deliverability first — protecting your sender reputation and your confidence.',
  },
  {
    icon: '✍',
    title: 'Sounds like you',
    body: "Drafts pull from your résumé and each contact's role — a founder gets a different email than a recruiter. Never fabricated facts.",
  },
  {
    icon: '✉',
    title: 'Your Gmail, not ours',
    body: 'Emails come from your real address with human-like pacing and a daily cap. Recipients see you, not a mailing tool.',
  },
  {
    icon: '☰',
    title: 'Pipeline built in',
    body: 'Drag contacts from Emailed to Replied to Interview to Offer. Your whole search in one board — no spreadsheet.',
  },
  {
    icon: '◉',
    title: 'Reply radar',
    body: 'Replies and bounces are detected automatically. Follow-ups cancel themselves the moment someone answers.',
  },
  {
    icon: '🔒',
    title: 'Private by design',
    body: 'Credentials encrypted at rest, no telemetry, and your résumé and contacts are never shared or sold.',
  },
]

const VALUES = [
  { icon: '🎯', title: 'Quality over volume', sub: '25 great emails beat 500 generic ones.' },
  { icon: '🤝', title: 'Respect the inbox', sub: 'Caps, pacing, and no double-sends — ever.' },
  { icon: '🔑', title: 'You own everything', sub: 'Your inbox, your contacts, your data. Export anytime.' },
]

const THEME_ICONS: Record<Theme, typeof Sun> = { light: Sun, dark: Moon, system: Monitor }

function scrollToSection(e: React.MouseEvent, id: string) {
  e.preventDefault()
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  document.getElementById(id)?.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth' })
}

export default function Landing({ onLogin, onSignup }: { onLogin: () => void; onSignup: () => void }) {
  const [theme, setTheme] = useState<Theme>(getStoredTheme)

  // Pipeline bars start at 0 width and transition to their targets shortly
  // after mount (CSS handles the easing + per-bar delay).
  const [barsIn, setBarsIn] = useState(false)
  useEffect(() => {
    const t = setTimeout(() => setBarsIn(true), 250)
    return () => clearTimeout(t)
  }, [])

  const ThemeIcon = THEME_ICONS[theme]

  return (
    <div className="crl-root">
      {/* ═══ Nav ═══ */}
      <nav className="crl-nav">
        <a
          className="crl-nav-logo"
          href="#top"
          aria-label="ColdReach — back to top"
          onClick={(e) => scrollToSection(e, 'top')}
        >
          <Logo size={26} wordmark />
        </a>
        <div className="crl-nav-links">
          <a href="#how" onClick={(e) => scrollToSection(e, 'how')}>How it works</a>
          <a href="#features" onClick={(e) => scrollToSection(e, 'features')}>Features</a>
          <a href="#about" onClick={(e) => scrollToSection(e, 'about')}>About</a>
        </div>
        <div className="crl-nav-spacer" />
        <button
          className="crl-theme-btn"
          type="button"
          title={`Theme: ${theme} — click to switch`}
          aria-label={`Theme: ${theme}. Switch theme`}
          onClick={() => setTheme(cycleTheme())}
        >
          <ThemeIcon size={15} aria-hidden />
        </button>
        <button className="crl-btn-login" type="button" onClick={onLogin}>Log in</button>
        <button className="crl-btn-signup" type="button" onClick={onSignup}>Sign up free</button>
      </nav>

      {/* ═══ Hero ═══ */}
      <header id="top" className="crl-hero">
        <div className="crl-up">
          <div className="crl-badge">FOR ENGINEERS ON THE JOB HUNT</div>
          <h1 className="crl-h1">
            Skip the ATS black hole.<br />
            <span className="crl-h1-accent">Email the people who decide.</span>
          </h1>
          <p className="crl-lede">
            ColdReach finds decision-makers at companies hiring right now, writes emails grounded
            in <em>your</em> résumé, sends from your own Gmail — and tracks every lead from sent
            to offer.
          </p>
          <div className="crl-cta-row">
            <button className="crl-btn-primary" type="button" onClick={onSignup}>Start free →</button>
            <a className="crl-btn-secondary" href="#how" onClick={(e) => scrollToSection(e, 'how')}>
              See how it works
            </a>
          </div>
          <div className="crl-trust">
            <span>✓ Your own Gmail</span>
            <span>✓ Your data stays yours</span>
            <span>✓ No credit card</span>
          </div>
        </div>

        {/* Hero visual: floating product cards */}
        <div className="crl-hero-visual crl-up-delay" aria-hidden>
          <div className="crl-pipeline-card crl-float">
            <div className="crl-mono-label">YOUR PIPELINE</div>
            <div className="crl-bars">
              {BARS.map((b) => (
                <div className="crl-bar-row" key={b.key}>
                  <span className="crl-bar-name">{b.label}</span>
                  <div className="crl-bar-track">
                    <div
                      className={`crl-bar-fill crl-bar-fill--${b.key}`}
                      style={{ width: barsIn ? b.width : '0%' }}
                    />
                  </div>
                  <span className={`crl-bar-num crl-bar-num--${b.key}`}>{b.count}</span>
                </div>
              ))}
            </div>
          </div>
          <div className="crl-toast-reply crl-float2">
            <span className="crl-toast-avatar">PN</span>
            <div>
              <div className="crl-toast-title">Priya replied 🎉</div>
              <div className="crl-toast-sub">"let's find 20 min this week"</div>
            </div>
          </div>
          <div className="crl-toast-sending crl-float-late">
            <span className="crl-pulse-dot" />
            <span className="crl-sending-text">
              Sending paced from <strong>you@gmail.com</strong>
            </span>
          </div>
        </div>
      </header>

      {/* ═══ Stats strip ═══ */}
      <div className="crl-stats-wrap">
        <div className="crl-stats">
          <div className="crl-stat-card">
            <div className="crl-stat-value crl-stat-value--accent">10×</div>
            <div className="crl-stat-caption">the reply rate of job-portal applications</div>
          </div>
          <div className="crl-stat-card">
            <div className="crl-stat-value crl-stat-value--teal">5 min</div>
            <div className="crl-stat-caption">from sign-up to your first personalised email</div>
          </div>
          <div className="crl-stat-card">
            <div className="crl-stat-value crl-stat-value--ok">100%</div>
            <div className="crl-stat-caption">sent from your own inbox — no third-party sender</div>
          </div>
        </div>
      </div>

      {/* ═══ How it works ═══ */}
      <section id="how" className="crl-section">
        <div className="crl-kicker">HOW IT WORKS</div>
        <h2 className="crl-h2">Four steps to interviews, on autopilot.</h2>
        <p className="crl-sub">
          No spreadsheets, no guessing emails, no copy-pasting the same intro forty times.
        </p>
        <div className="crl-steps">
          {STEPS.map((s) => (
            <div className="crl-step-card" key={s.n}>
              <div className={`crl-step-num crl-step-num--${s.n}`}>{s.n}</div>
              <div className="crl-card-title">{s.title}</div>
              <p className="crl-card-body">{s.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ═══ Features ═══ */}
      <section id="features" className="crl-features-band">
        <div className="crl-features-inner">
          <div className="crl-kicker">FEATURES</div>
          <h2 className="crl-h2">Everything between "hiring?" and "hired."</h2>
          <div className="crl-feature-grid">
            {FEATURES.map((f) => (
              <div className="crl-feature-card" key={f.title}>
                <div className="crl-feature-icon" aria-hidden>{f.icon}</div>
                <div className="crl-card-title">{f.title}</div>
                <p className="crl-card-body">{f.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ═══ About ═══ */}
      <section id="about" className="crl-about">
        <div>
          <div className="crl-kicker">ABOUT</div>
          <h2 className="crl-h2">Built by a job-seeker who got tired of applying into the void.</h2>
          <p className="crl-about-p">
            After 200 applications and 3 replies, we tried something else: short, honest emails to
            the actual humans doing the hiring. The reply rate went up 10×. ColdReach is that
            workflow, productised — for early-career engineers reaching out to startups.
          </p>
          <p className="crl-about-p">
            It's built for genuine one-to-one outreach, not spam: daily caps, paced sending,
            verified addresses only, and a hard guard against emailing the same person twice.
          </p>
        </div>
        <div className="crl-values">
          {VALUES.map((v) => (
            <div className="crl-value-card" key={v.title}>
              <span className="crl-value-icon" aria-hidden>{v.icon}</span>
              <div>
                <div className="crl-value-title">{v.title}</div>
                <div className="crl-value-sub">{v.sub}</div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* ═══ CTA band ═══ */}
      <section className="crl-cta-wrap">
        <div className="crl-cta-band">
          <h2>Your next role is one good email away.</h2>
          <p>Free to start. Five minutes to your first send.</p>
          <button className="crl-btn-cta" type="button" onClick={onSignup}>Sign up free →</button>
        </div>
      </section>

      {/* ═══ Footer ═══ */}
      <footer className="crl-footer">
        <Logo size={26} wordmark />
        <span>© 2026</span>
        <div className="crl-nav-spacer" />
        <a href="#how" onClick={(e) => scrollToSection(e, 'how')}>How it works</a>
        <a href="#features" onClick={(e) => scrollToSection(e, 'features')}>Features</a>
        <a href="#about" onClick={(e) => scrollToSection(e, 'about')}>About</a>
        <button className="crl-footer-login" type="button" onClick={onLogin}>Log in</button>
      </footer>
    </div>
  )
}
