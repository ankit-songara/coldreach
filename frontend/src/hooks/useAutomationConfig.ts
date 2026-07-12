import { useQuery } from '@tanstack/react-query'
import { automationApi } from '../api/automation'

// Shared server-config query (sender name, signature links, Gmail connection).
// Today, Setup, and Send each need it on mount; before, each fired its own
// GET /config. One cached fetch now serves all three. Mutations that return a
// fresh ConfigStatus should write it back with
//   queryClient.setQueryData(['config'], cfg)
// so every consumer updates without a refetch.
export function useAutomationConfig(enabled = true) {
  return useQuery({
    queryKey: ['config'],
    queryFn:  automationApi.getConfig,
    enabled,
  })
}
