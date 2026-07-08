import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import toast from 'react-hot-toast'
import type { Contact, Draft } from '../types'
import { getToken, setToken } from '../api/client'
import { authApi } from '../api/auth'
import { huntApi } from '../api/hunt'
import { contactsApi } from '../api/contacts'
import { queryClient } from '../lib/queryClient'

// Staged status while a hunt runs — generic on purpose (no source names).
const HUNT_STAGES = [
  { after: 0,      label: 'Searching for companies hiring right now…' },
  { after: 12_000, label: 'Matching people to your query…' },
  { after: 25_000, label: 'Finding and checking email addresses…' },
  { after: 40_000, label: 'Almost there — putting your results together…' },
]

type TabId = 'today' | 'setup' | 'hunt' | 'compose' | 'send'

// Initial tab comes from the URL hash so a reload (or shared link) lands on
// the right tab. Starting at 'today' and correcting in an effect loses the
// race against the hash-sync effect, which rewrites the URL first.
const initialTab = ((): TabId => {
  const h = window.location.hash.replace('#', '')
  return (['today', 'setup', 'hunt', 'compose', 'send'] as const).includes(h as TabId)
    ? (h as TabId) : 'today'
})()

interface AppState {
  // ── Auth ──────────────────────────────────────────────────────────────────
  token:      string | null
  userEmail:  string
  setAuth:    (token: string, email: string) => void
  logout:     () => void

  // ── Gmail ─────────────────────────────────────────────────────────────────
  gmailAddress:      string
  gmailAppPassword:  string
  setGmailCreds: (address: string, password: string) => void

  // ── Resume ────────────────────────────────────────────────────────────────
  resume: string
  setResume: (text: string) => void

  // ── Contacts ─────────────────────────────────────────────────────────────
  contacts:    Contact[]
  setContacts: (c: Contact[]) => void
  upsertContact: (c: Contact) => void
  removeContact: (id: number) => void
  clearContacts: () => void

  // ── Drafts ────────────────────────────────────────────────────────────────
  drafts:    Record<number, Draft[]>  // keyed by contact_id
  setDrafts: (contactId: number, drafts: Draft[]) => void

  // ── Hunt (lives here so it survives tab switches — the request keeps
  //    running and results/progress are there when the user comes back) ──────
  hunting:     boolean
  huntStage:   string
  huntResults: Contact[] | null   // results of the LAST hunt (null = none yet)
  huntInfo:    { found: number; duplicates: number; query: string } | null
  runHunt:          (query: string) => Promise<void>
  clearHunt:        () => void
  removeHuntResult: (id: number) => void
  updateHuntResult: (c: Contact) => void

  // ── UI ────────────────────────────────────────────────────────────────────
  activeTab:    TabId
  setActiveTab: (tab: AppState['activeTab']) => void
}

export const useStore = create<AppState>()(
  persist(
    (set, get) => ({
      token:     getToken(),
      userEmail: '',
      setAuth: (token, email) => {
        setToken(token)
        set({ token, userEmail: email })
      },
      logout: () => {
        // Revoke the token server-side (best-effort). The token is captured and
        // sent explicitly because we clear localStorage before the request's
        // interceptor would read it (see authApi.logout).
        const token = getToken()
        if (token) void authApi.logout(token)
        setToken(null)
        // Reset EVERY per-user field, not just auth/contacts. `resume` is
        // persisted (see partialize below) — leaving it out meant the next
        // person to log in on this browser inherited the previous user's
        // résumé, and App.tsx's hydration effect skips its own fetch whenever
        // `resume` is already non-empty, so the wrong résumé stuck around
        // silently until someone noticed and manually overwrote it.
        set({
          token: null, userEmail: '', contacts: [], drafts: {},
          hunting: false, huntStage: '', huntResults: null, huntInfo: null,
          resume: '', gmailAddress: '', gmailAppPassword: '',
        })
        // Drop cached query results too — ['contacts'] etc. have no user
        // segment in their key, so within staleTime a fresh login could
        // otherwise serve the previous account's cached response.
        queryClient.clear()
      },

      gmailAddress:     '',
      gmailAppPassword: '',
      setGmailCreds: (address, password) =>
        set({ gmailAddress: address, gmailAppPassword: password }),

      resume:    '',
      setResume: (text) => set({ resume: text }),

      contacts:    [],
      setContacts: (contacts) => set({ contacts }),
      upsertContact: (contact) =>
        set((s) => ({
          contacts: s.contacts.some(c => c.id === contact.id)
            ? s.contacts.map(c => c.id === contact.id ? contact : c)
            : [...s.contacts, contact],
        })),
      removeContact: (id) =>
        set((s) => ({ contacts: s.contacts.filter(c => c.id !== id) })),
      clearContacts: () => set({ contacts: [], drafts: {} }),

      drafts:    {},
      setDrafts: (contactId, drafts) =>
        set((s) => ({ drafts: { ...s.drafts, [contactId]: drafts } })),

      // ── Hunt ──────────────────────────────────────────────────────────────
      hunting:     false,
      huntStage:   '',
      huntResults: null,
      huntInfo:    null,
      runHunt: async (query) => {
        if (get().hunting) return
        set({ hunting: true, huntResults: null, huntInfo: null, huntStage: HUNT_STAGES[0].label })
        const timers = HUNT_STAGES.slice(1).map(s =>
          setTimeout(() => { if (get().hunting) set({ huntStage: s.label }) }, s.after)
        )
        try {
          const data = await huntApi.hunt({ query })
          set({
            huntResults: (data.contacts ?? []) as Contact[],
            huntInfo: { found: data.found ?? 0, duplicates: data.duplicates ?? 0, query },
          })
          try { set({ contacts: await contactsApi.list() }) } catch { /* refetch later */ }
          queryClient.invalidateQueries({ queryKey: ['contacts'] })
          // Toasts fire from here so completion is announced on ANY tab.
          if (data.total > 0) {
            toast.success(`Found ${data.total} new contact${data.total !== 1 ? 's' : ''} — see the Hunt tab`)
          } else if ((data.duplicates ?? 0) > 0) {
            toast('Every match is already in your list', { icon: '✅' })
          } else if ((data.found ?? 0) > 0) {
            toast('Found roles, but no direct email — details in the Hunt tab', { icon: '📭' })
          } else {
            toast(`No matches for "${query}"`, { icon: '🔍' })
          }
        } catch (e: any) {
          toast.error(e.message)
        } finally {
          timers.forEach(clearTimeout)
          set({ hunting: false, huntStage: '' })
        }
      },
      clearHunt: () => set({ huntResults: null, huntInfo: null }),
      removeHuntResult: (id) =>
        set((s) => ({ huntResults: s.huntResults?.filter(c => c.id !== id) ?? null })),
      updateHuntResult: (contact) =>
        set((s) => ({
          huntResults: s.huntResults?.map(c => c.id === contact.id ? { ...c, ...contact } : c) ?? null,
        })),

      activeTab:    initialTab,
      setActiveTab: (activeTab) => set({ activeTab }),
    }),
    {
      name: 'coldreach-store',
      version: 1,
      // One-time migration: any localStorage entries written before v1 may
      // contain gmailAddress + gmailAppPassword. Strip them on load.
      migrate: (persistedState, fromVersion) => {
        if (fromVersion < 1 && persistedState && typeof persistedState === 'object') {
          const s = persistedState as Partial<AppState>
          delete s.gmailAddress
          delete s.gmailAppPassword
        }
        return persistedState as AppState
      },
      // ⚠️  Do NOT persist gmailAppPassword / gmailAddress to localStorage.
      // Gmail App Passwords are credentials — if this browser is compromised
      // (XSS, malicious extension, shared device) the password leaks with the
      // rest of the store. Resume text is fine to persist.
      partialize: (s) => ({
        resume: s.resume,
        userEmail: s.userEmail,
      }),
    },
  ),
)
