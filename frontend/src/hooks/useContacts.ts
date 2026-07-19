import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { contactsApi } from '../api/contacts'
import { useStore } from '../store'

// Single source of truth for the contact list. App, Today, and Hunt all need
// contacts on mount; before, each fired its own GET /contacts — three requests
// per load once keep-alive kept every visited tab mounted. React Query dedupes
// them to one shared fetch, mirrored into the Zustand store so everything else
// keeps reading `contacts` synchronously, exactly as before.
export function useContacts(enabled = true) {
  const setContacts = useStore(s => s.setContacts)

  const { data, isFetched, isError, refetch } = useQuery({
    queryKey: ['contacts'],
    queryFn:  contactsApi.list,
    enabled,
  })

  useEffect(() => {
    if (data) setContacts(data)
  }, [data, setContacts])

  // contactsLoaded is true once the first fetch settled (success OR error) —
  // skeleton gating. contactsError lets callers distinguish "you have no
  // contacts" from "the request failed": without it, a transient GET failure
  // rendered a returning user as brand-new (onboarding view, zeroed funnel).
  return { contactsLoaded: isFetched, contactsError: isError, refetchContacts: refetch }
}
