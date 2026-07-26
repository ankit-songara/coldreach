import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { composeApi } from '../api/compose'
import { getToken } from '../api/client'
import { useStore } from '../store'
import type { Draft } from '../types'

// Single source of truth for "every draft the user has". Compose and Send both
// need this on mount. Before, each fired GET /compose/drafts/all in its own
// effect — and once keep-alive kept both tabs mounted, that doubled the request
// on load. React Query dedupes it to ONE in-flight request + one cached result
// (shared by key), and the result is mirrored into the Zustand store so the rest
// of the components keep reading `drafts` synchronously, exactly as before.
export function useAllDrafts() {
  const contacts  = useStore(s => s.contacts)
  const setDrafts = useStore(s => s.setDrafts)
  const hasContacts = contacts.length > 0

  const { data, isFetched } = useQuery({
    queryKey: ['drafts', 'all'],
    queryFn:  composeApi.getAllDrafts,
    // Gate on auth, not contact-count: gating on `hasContacts` forced this to
    // wait for GET /contacts to resolve first (a serial waterfall on Compose/
    // Send first paint). The endpoint 200s [] for a brand-new user, so the only
    // cost of firing in parallel is one cheap empty call for the zero-contact
    // case — in exchange for parallelizing the common path.
    enabled:  !!getToken(),
  })

  // Fan the flat list back out into the store's per-contact shape.
  useEffect(() => {
    if (!data) return
    const grouped: Record<number, Draft[]> = {}
    for (const d of data) (grouped[d.contact_id] ??= []).push(d)
    Object.entries(grouped).forEach(([cid, ds]) => setDrafts(Number(cid), ds))
  }, [data, setDrafts])

  // "Loaded" once there's nothing to fetch, or the one fetch has settled — the
  // flag both tabs use to swap their loading skeletons for real content.
  return { draftsLoaded: !hasContacts || isFetched }
}
