import { useQuery } from '@tanstack/react-query'
import { Lightbulb } from 'lucide-react'
import { analyticsApi } from '../../api/analytics'
import type { SendTimeCell } from '../../api/analytics'

const DAY_SHORT = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']
const DAY_FULL  = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
const PARTS = ['morning', 'afternoon', 'evening'] as const

// A best-slot claim needs a minimum sample — below this we say nothing rather
// than crown a slot off one lucky send.
const MIN_SLOT_SAMPLE = 5

const pct = (rate: number) => `${Math.round(rate * 100)}%`

// '2026-01-12' → '12 Jan'. Date-only strings get a fixed midnight so the label
// never slips a day across timezones.
function weekLabel(weekStart: string): string {
  return new Date(weekStart + 'T00:00:00')
    .toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

function PanelTitle({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-xs font-bold font-mono tracking-widest mb-4" style={{ color: 'var(--text-muted)' }}>
      {children}
    </div>
  )
}

export default function Analytics() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['analytics'],
    queryFn: analyticsApi.summary,
  })

  if (isLoading) return (
    <div className="space-y-5 animate-pulse" aria-hidden>
      <div>
        <div className="h-8 rounded w-36 mb-2" style={{ background: 'var(--surface-3)' }} />
        <div className="h-4 rounded w-64" style={{ background: 'var(--surface-2)' }} />
      </div>
      <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(96px, 1fr))' }}>
        {[0, 1, 2, 3, 4].map(i => (
          <div key={i} className="card text-center" style={{ padding: 16 }}>
            <div className="h-8 rounded w-12 mx-auto mb-2" style={{ background: 'var(--surface-3)' }} />
            <div className="h-3 rounded w-16 mx-auto" style={{ background: 'var(--surface-2)' }} />
          </div>
        ))}
      </div>
      <div className="grid gap-3.5" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))' }}>
        <div className="h-56 rounded-2xl" style={{ background: 'var(--surface-2)' }} />
        <div className="h-56 rounded-2xl" style={{ background: 'var(--surface-2)' }} />
      </div>
      <div className="h-48 rounded-2xl" style={{ background: 'var(--surface-2)' }} />
    </div>
  )

  if (isError || !data) return (
    <div className="flex flex-col items-center py-20 px-6 text-center">
      <p className="text-sm mb-4" style={{ color: 'var(--text-muted)' }}>
        Couldn't load your analytics — check your connection and try again.
      </p>
      <button
        onClick={() => refetch()}
        className="px-5 py-2 rounded-full text-sm font-bold"
        style={{ background: 'var(--accent)', color: 'var(--on-accent)', border: 'none', cursor: 'pointer' }}
      >
        Retry
      </button>
    </div>
  )

  const { weekly, send_time, by_role, totals } = data
  const allZero = totals.sent === 0

  // ── Weekly bars ────────────────────────────────────────────────────────────
  const maxWeekRate = Math.max(...weekly.map(w => w.rate), 0)

  // ── Heatmap ────────────────────────────────────────────────────────────────
  const cell = new Map<string, SendTimeCell>()
  for (const c of send_time) cell.set(`${c.weekday}-${c.part}`, c)
  const daySent = (wd: number) =>
    PARTS.reduce((n, p) => n + (cell.get(`${wd}-${p}`)?.sent ?? 0), 0)
  // Weekend rows only earn their space once something was actually sent then.
  const showWeekend = daySent(5) > 0 || daySent(6) > 0
  const days = showWeekend ? [0, 1, 2, 3, 4, 5, 6] : [0, 1, 2, 3, 4]

  // Best-slot line — computed, never asserted: only when a cell has a real
  // sample (sent ≥ 5) and at least one reply.
  const candidates = send_time.filter(c => c.sent >= MIN_SLOT_SAMPLE && c.replied > 0)
  const best = candidates.length
    ? candidates.reduce((a, b) => (b.replied / b.sent > a.replied / a.sent ? b : a))
    : null

  // ── Roles ──────────────────────────────────────────────────────────────────
  const maxRoleRate = Math.max(...by_role.map(r => r.rate), 0)

  return (
    <div className="space-y-5">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div>
        <h1 className="text-2xl font-bold tracking-wide mb-1" style={{ fontFamily: 'var(--font-display)' }}>Analytics</h1>
        <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
          Where replies come from, how you're trending, and when to hit send.
        </p>
      </div>

      {/* ── Totals strip ───────────────────────────────────────────────────── */}
      <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(96px, 1fr))' }}>
        {[
          { label: 'Sent',       value: String(totals.sent),        color: 'var(--accent-text)' },
          { label: 'Replied',    value: String(totals.replied),     color: 'var(--status-replied)' },
          { label: 'Interviews', value: String(totals.interviews),  color: 'var(--status-interview)' },
          { label: 'Offers',     value: String(totals.offers),      color: 'var(--status-offer)' },
          { label: 'Reply rate', value: pct(totals.reply_rate),     color: 'var(--text)' },
        ].map(stat => (
          <div key={stat.label} className="card text-center">
            <div className="text-2xl font-bold tnum" style={{ color: stat.color }}>{stat.value}</div>
            <div className="text-xs font-mono mt-1" style={{ color: 'var(--text-muted)' }}>{stat.label}</div>
          </div>
        ))}
      </div>

      {/* ── Nothing-sent-yet note ──────────────────────────────────────────── */}
      {allZero && (
        <div
          className="flex items-center gap-3 rounded-xl px-4 py-3"
          style={{ background: 'var(--surface-2)', border: '1px dashed var(--border-strong)' }}
        >
          <Lightbulb size={15} style={{ color: 'var(--warning)', flexShrink: 0 }} />
          <span className="text-sm" style={{ color: 'var(--text-muted)' }}>
            Send your first emails and this fills in — every chart here is computed from your real outreach.
          </span>
        </div>
      )}

      <div className="grid gap-3.5" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))' }}>
        {/* ── Panel 1 · weekly trend ───────────────────────────────────────── */}
        <div className="card">
          <PanelTitle>REPLY RATE · LAST 6 WEEKS</PanelTitle>
          <div className="flex items-end gap-2.5" style={{ height: 130 }}>
            {weekly.map(w => {
              const empty = w.sent === 0
              // Tallest week fills the panel; the rest scale to it. Non-empty
              // weeks keep a visible stub even at 0% so "sent, no replies yet"
              // reads differently from "nothing sent".
              const h = empty || maxWeekRate === 0
                ? 3
                : Math.max(6, Math.round((w.rate / maxWeekRate) * 100))
              return (
                <div key={w.week_start} className="flex-1 flex flex-col items-center justify-end gap-1.5 h-full min-w-0">
                  <span
                    className="text-[11px] font-bold tnum"
                    style={{ color: empty ? 'var(--text-dim)' : 'var(--accent-text)' }}
                  >
                    {empty ? '–' : pct(w.rate)}
                  </span>
                  <div
                    title={`Week of ${weekLabel(w.week_start)}: ${w.replied} of ${w.sent} sent got a reply`}
                    style={{
                      width: '100%',
                      height: empty || maxWeekRate === 0 ? h : `${h}%`,
                      borderRadius: '7px 7px 3px 3px',
                      background: empty ? 'var(--surface-3)' : 'var(--accent)',
                      transition: 'height 0.7s var(--ease-out)',
                    }}
                  />
                  <span className="text-[9.5px] font-mono" style={{ color: 'var(--text-dim)' }}>
                    {weekLabel(w.week_start)}
                  </span>
                </div>
              )
            })}
          </div>
        </div>

        {/* ── Panel 2 · send-time heatmap ──────────────────────────────────── */}
        <div className="card">
          <PanelTitle>REPLIES BY SEND TIME</PanelTitle>
          <div className="grid gap-1.5" style={{ gridTemplateColumns: '40px repeat(3, 1fr)' }}>
            <span />
            {PARTS.map(p => (
              <span key={p} className="text-center text-[9.5px] font-mono" style={{ color: 'var(--text-dim)' }}>
                {p.toUpperCase()}
              </span>
            ))}
            {days.map(wd => (
              <div key={wd} className="contents">
                <span className="self-center text-[10px] font-mono" style={{ color: 'var(--text-dim)' }}>
                  {DAY_SHORT[wd]}
                </span>
                {PARTS.map(p => {
                  const c = cell.get(`${wd}-${p}`)
                  const sent = c?.sent ?? 0
                  const replied = c?.replied ?? 0
                  const ratio = sent > 0 ? replied / sent : 0
                  return (
                    <div
                      key={p}
                      title={`${DAY_FULL[wd]} ${p}: ${replied} of ${sent} sent got a reply`}
                      className="flex items-center justify-center rounded-lg text-[11px] tnum"
                      style={{
                        height: 32,
                        // Ratio 0 lands exactly on surface-2; capped at 60% so
                        // the counts stay readable on top in both themes.
                        background: `color-mix(in srgb, var(--accent) ${Math.round(ratio * 60)}%, var(--surface-2))`,
                        color: sent > 0 ? 'var(--text)' : 'var(--text-dim)',
                      }}
                    >
                      {sent > 0 ? `${replied}/${sent}` : ''}
                    </div>
                  )
                })}
              </div>
            ))}
          </div>
          {best && (
            <p className="text-xs mt-3" style={{ color: 'var(--text-muted)', margin: '12px 0 0' }}>
              Best so far: <strong style={{ color: 'var(--text)' }}>{DAY_FULL[best.weekday]} {best.part}s</strong>
              {' '}— {pct(best.replied / best.sent)} reply rate ({best.replied} of {best.sent}).
            </p>
          )}
        </div>
      </div>

      {/* ── Panel 3 · who replies most ─────────────────────────────────────── */}
      <div className="card">
        <PanelTitle>WHO REPLIES MOST</PanelTitle>
        {by_role.length === 0 ? (
          <p className="text-sm font-mono text-center py-6" style={{ color: 'var(--text-dim)', margin: 0 }}>
            No sends yet
          </p>
        ) : by_role.map((r, i) => (
          <div
            key={r.family}
            className="flex items-center gap-3"
            style={{ padding: '9px 0', borderBottom: i < by_role.length - 1 ? '1px solid var(--border)' : 'none' }}
          >
            <div style={{ width: 104, flexShrink: 0 }}>
              <div className="text-[13px] font-semibold capitalize truncate">{r.family}</div>
              <div className="text-[10.5px] font-mono tnum" style={{ color: 'var(--text-dim)' }}>
                {r.replied} of {r.sent} sent
              </div>
            </div>
            <div className="flex-1" style={{ height: 8, borderRadius: 99, background: 'var(--surface-2)', overflow: 'hidden' }}>
              <div
                style={{
                  height: '100%',
                  borderRadius: 99,
                  background: 'var(--status-replied)',
                  width: maxRoleRate > 0 ? `${Math.round((r.rate / maxRoleRate) * 100)}%` : 0,
                  minWidth: r.replied > 0 ? 8 : 0,
                  transition: 'width 0.7s var(--ease-out)',
                }}
              />
            </div>
            <span
              className="text-sm font-extrabold tnum text-right flex-shrink-0"
              style={{ width: 44, color: 'var(--status-replied)' }}
            >
              {pct(r.rate)}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
