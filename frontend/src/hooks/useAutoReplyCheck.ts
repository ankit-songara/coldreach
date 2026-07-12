import { useEffect, useRef } from 'react'
import toast from 'react-hot-toast'
import { useQueryClient } from '@tanstack/react-query'
import { inboxApi } from '../api/inbox'
import { contactsApi } from '../api/contacts'
import { useStore } from '../store'

const LAST_SYNC_KEY = 'coldreach-last-inbox-sync'
const MIN_GAP_MS = 30 * 60 * 1000   // sync at most twice an hour

// Quietly scan the Gmail inbox for replies when the app opens, so the Today
// alerts ("3 new replies") are fresh without the user remembering to press
// "Check Replies". Runs only when creds are stored server-side (encrypted),
// throttled via localStorage, and never surfaces errors — it's a background
// nicety, not a feature the user asked for right now.
export function useAutoReplyCheck(hasServerGmail: boolean) {
  const setContacts = useStore(s => s.setContacts)
  const qc = useQueryClient()
  const ran = useRef(false)

  useEffect(() => {
    if (!hasServerGmail || ran.current) return
    const last = Number(localStorage.getItem(LAST_SYNC_KEY) || 0)
    if (Date.now() - last < MIN_GAP_MS) return
    ran.current = true
    // Stamp BEFORE the request so a slow/failing IMAP scan can't retrigger
    // on every reload and hammer the server.
    localStorage.setItem(LAST_SYNC_KEY, String(Date.now()))

    inboxApi.sync('', '')   // empty creds → the server-stored (encrypted) ones
      .then(async res => {
        if (res.replies_found === 0) return
        toast.success(
          `${res.replies_found} new ${res.replies_found === 1 ? 'reply' : 'replies'} since you were away`,
          { duration: 6000 },
        )
        try { setContacts(await contactsApi.list()) } catch { /* next refetch */ }
        qc.invalidateQueries({ queryKey: ['contacts'] })
      })
      .catch(() => { /* silent — the manual "Check Replies" button still exists */ })
  }, [hasServerGmail]) // eslint-disable-line react-hooks/exhaustive-deps
}
