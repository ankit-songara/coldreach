import api from './client'

export type DayPart = 'morning' | 'afternoon' | 'evening'

export interface WeekPoint {
  week_start: string   // 'YYYY-MM-DD' (Monday, ISO week)
  sent:       number
  replied:    number
  rate:       number   // 0..1
}

export interface SendTimeCell {
  weekday: number      // 0 = Mon … 6 = Sun
  part:    DayPart
  sent:    number
  replied: number
}

export interface RoleRow {
  family:  string
  sent:    number
  replied: number
  rate:    number      // 0..1
}

export interface AnalyticsTotals {
  sent:       number
  replied:    number
  interviews: number
  offers:     number
  reply_rate: number   // 0..1
}

export interface AnalyticsSummary {
  weekly:    WeekPoint[]      // 6 entries, oldest → newest
  send_time: SendTimeCell[]   // 7 weekdays × 3 parts = 21 cells
  by_role:   RoleRow[]        // sorted by rate desc
  totals:    AnalyticsTotals
}

export const analyticsApi = {
  summary: () => api.get<AnalyticsSummary>('/analytics/summary').then(r => r.data),
}
