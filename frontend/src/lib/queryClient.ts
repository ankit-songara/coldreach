import { QueryClient } from '@tanstack/react-query'

// Shared instance so non-component code (store actions) can invalidate caches.
export const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
})
