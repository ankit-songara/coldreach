import { QueryClient } from '@tanstack/react-query'

// Shared instance so non-component code (store actions) can invalidate caches.
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Every visited tab stays mounted (App keep-alive), so a refetch hits
      // ALL cached queries at once. 60s staleTime + no focus refetch stops the
      // storm of contacts/config/drafts/replies requests on every alt-tab.
      // Explicit invalidations (hunt done, send, reply actions) bypass
      // staleTime and refetch immediately, and per-query overrides still win
      // (Today's /health check opts back INTO focus refetch).
      staleTime: 60_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})
