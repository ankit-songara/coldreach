import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Zap, Clock, Trash2, CalendarClock } from 'lucide-react'
import { useStore } from '../../store'
import { automationApi } from '../../api/automation'
import type { ConfigStatus, ScheduledItem } from '../../api/automation'

/**
 * Follow-up automation: enable server-side sending, queue nudges N days out,
 * and view/cancel the pending queue. Replies auto-cancel follow-ups (backend).
 */
export default function AutomationPanel() {
  const { gmailAddress, gmailAppPassword } = useStore()
  const qc = useQueryClient()
  const [config, setConfig] = useState<ConfigStatus | null>(null)
  const [queue, setQueue]   = useState<ScheduledItem[]>([])
  const [days, setDays]     = useState(3)
  const [busy, setBusy]     = useState(false)

  const refresh = async () => {
    try {
      const [cfg, q] = await Promise.all([
        automationApi.getConfig(),
        automationApi.listFollowups(),
      ])
      setConfig(cfg); setQueue(q)
    } catch { /* backend may be starting */ }
  }

  useEffect(() => { refresh() }, [])

  const enableAutomation = async () => {
    if (!gmailAddress || !gmailAppPassword) {
      toast.error('Add Gmail credentials in Setup first'); return
    }
    setBusy(true)
    try {
      // Push the browser-held creds to the server so the scheduler can send
      await automationApi.saveGmail(gmailAddress, gmailAppPassword)
      const cfg = await automationApi.setAutomation({ enabled: true })
      setConfig(cfg)
      toast.success('Automation enabled — follow-ups will send automatically')
    } catch (e: any) {
      toast.error(e.response?.data?.detail ?? e.message)
    } finally { setBusy(false) }
  }

  const disableAutomation = async () => {
    setBusy(true)
    try {
      setConfig(await automationApi.setAutomation({ enabled: false }))
      toast('Automation paused', { icon: '⏸️' })
    } catch (e: any) {
      toast.error(e.response?.data?.detail ?? e.message)
    } finally { setBusy(false) }
  }

  const scheduleAll = async () => {
    setBusy(true)
    try {
      const res = await automationApi.scheduleFollowups([], days)
      if (res.scheduled === 0) {
        toast(`Nothing to schedule (${res.skipped} skipped)`, { icon: '📭' })
      } else {
        toast.success(`Queued ${res.scheduled} follow-ups for ${days}d out`)
      }
      await refresh()
    } catch (e: any) {
      toast.error(e.response?.data?.detail ?? e.message)
    } finally { setBusy(false) }
  }

  const cancel = async (id: number) => {
    try {
      await automationApi.cancelFollowup(id)
      setQueue(q => q.filter(i => i.id !== id))
    } catch (e: any) {
      toast.error(e.response?.data?.detail ?? e.message)
    }
  }

  const enabled = config?.automation_enabled

  return (
    <div className="card space-y-4" style={{ border: '1px solid rgba(167,139,250,0.22)' }}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Zap size={15} style={{ color: '#a78bfa' }} />
          <span className="text-sm font-bold tracking-wide" style={{ fontFamily: 'Rajdhani' }}>
            Follow-up Automation
          </span>
          <span
            className="badge"
            style={{
              background: enabled ? 'rgba(52,211,153,0.12)' : 'rgba(100,116,139,0.12)',
              color: enabled ? '#34d399' : 'var(--text-dim)', fontSize: '9px',
            }}
          >
            {enabled ? 'ON' : 'OFF'}
          </span>
        </div>
        <button
          onClick={enabled ? disableAutomation : enableAutomation}
          disabled={busy}
          className="btn text-xs font-semibold"
          style={{
            background: enabled ? 'rgba(100,116,139,0.12)' : 'rgba(167,139,250,0.14)',
            borderColor: enabled ? 'var(--border)' : 'rgba(167,139,250,0.35)',
            color: enabled ? 'var(--text-dim)' : '#a78bfa',
          }}
        >
          {enabled ? 'Pause' : 'Enable'}
        </button>
      </div>

      <div className="flex items-center justify-between gap-3 flex-wrap">
        <p className="text-xs flex-1" style={{ color: 'var(--text-muted)' }}>
          When on, ColdReach sends queued follow-ups on schedule and stops the moment
          a contact replies.
        </p>
        <div className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--text-muted)' }}>
          Daily cap
          <input
            type="number"
            min={1}
            max={500}
            defaultValue={config?.daily_send_cap ?? 50}
            key={config?.daily_send_cap}
            onBlur={async e => {
              const cap = Number(e.target.value)
              if (cap > 0 && cap !== config?.daily_send_cap) {
                try {
                  setConfig(await automationApi.setAutomation({ daily_send_cap: cap }))
                  toast.success(`Daily cap set to ${cap}`)
                } catch (err: any) { toast.error(err.response?.data?.detail ?? err.message) }
              }
            }}
            className="input text-xs"
            style={{ width: '64px', padding: '4px 8px' }}
          />
          /24h
        </div>
      </div>

      {/* ── Schedule controls ── */}
      <div className="flex items-center gap-2 flex-wrap">
        <div className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--text-muted)' }}>
          <Clock size={12} /> Send follow-up after
        </div>
        <select
          value={days}
          onChange={e => setDays(Number(e.target.value))}
          className="input text-xs"
          style={{ padding: '4px 8px', width: 'auto' }}
        >
          {[1, 2, 3, 4, 5, 7, 10, 14].map(d => <option key={d} value={d}>{d} day{d > 1 ? 's' : ''}</option>)}
        </select>
        <button
          onClick={scheduleAll}
          disabled={busy}
          className="btn text-xs flex items-center gap-1.5 font-semibold"
          style={{ background: 'rgba(167,139,250,0.12)', borderColor: 'rgba(167,139,250,0.3)', color: '#a78bfa' }}
        >
          <CalendarClock size={12} /> Schedule for all emailed
        </button>
      </div>

      {/* ── Pending queue ── */}
      {queue.length > 0 && (
        <div className="space-y-1.5 pt-1">
          <div className="text-xs font-bold font-mono tracking-widest" style={{ color: 'var(--text-dim)' }}>
            QUEUED ({queue.length})
          </div>
          {queue.map(item => (
            <div key={item.id} className="flex items-center gap-3 text-xs">
              <CalendarClock size={12} style={{ color: '#a78bfa', flexShrink: 0 }} />
              <span className="flex-1 truncate">
                {item.name} · <span style={{ color: 'var(--text-dim)' }}>{item.email}</span>
              </span>
              <span className="font-mono flex-shrink-0" style={{ color: 'var(--text-dim)' }}>
                {new Date(item.send_at).toLocaleDateString()}
              </span>
              <button
                onClick={() => cancel(item.id)}
                title="Cancel"
                className="flex-shrink-0 w-5 h-5 flex items-center justify-center rounded"
                style={{ color: '#64748b' }}
              >
                <Trash2 size={11} />
              </button>
            </div>
          ))}
        </div>
      )}

      {enabled && (
        <p className="text-xs" style={{ color: '#f59e0b' }}>
          ⚠️ Your Gmail App Password is stored encrypted on this machine so the
          scheduler can send while the app runs. Pause to clear automated sending.
        </p>
      )}
      <button onClick={() => { qc.invalidateQueries({ queryKey: ['contacts'] }); refresh() }} className="hidden" />
    </div>
  )
}
