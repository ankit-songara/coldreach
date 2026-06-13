import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { Contact, Draft } from '../types'
import { getToken, setToken } from '../api/client'
import { authApi } from '../api/auth'

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

  // ── UI ────────────────────────────────────────────────────────────────────
  activeTab:    'setup' | 'hunt' | 'compose' | 'send'
  setActiveTab: (tab: AppState['activeTab']) => void
}

export const useStore = create<AppState>()(
  persist(
    (set) => ({
      token:     getToken(),
      userEmail: '',
      setAuth: (token, email) => {
        setToken(token)
        set({ token, userEmail: email })
      },
      logout: () => {
        // Revoke the token server-side (best-effort) before clearing it locally.
        void authApi.logout()
        setToken(null)
        set({ token: null, userEmail: '', contacts: [], drafts: {} })
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

      activeTab:    'setup',
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
