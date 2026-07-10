import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { composeApi } from '../api/compose'
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
    // Nothing to fetch with no contacts — and the endpoint would just 200 [].
    enabled:  hasContacts,
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
