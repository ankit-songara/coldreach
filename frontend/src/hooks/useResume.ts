import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { resumeApi } from '../api/resume'
import { useStore } from '../store'

// Single source of truth for résumé hydration. App.tsx used to call
// resumeApi.getLatest() in a raw effect — under StrictMode's double-mount that
// fired GET /resume/latest twice on every load, and any future consumer would
// have added a third. React Query dedupes by key (['resume']) so all mounts
// share ONE request, and the result is mirrored into the Zustand store so
// Compose/Setup keep reading `resume` synchronously, exactly as before.
//
// staleTime Infinity: the résumé only changes through Setup, which writes the
// store directly — background refetches would just re-download the same text
// (and could stomp a deliberate local clear, e.g. after "clear sample data").
export function useResume(enabled = true) {
  const resume    = useStore(s => s.resume)
  const setResume = useStore(s => s.setResume)
  const hasLocal  = !!resume.trim()

  const { data, isFetched } = useQuery({
    queryKey:  ['resume'],
    queryFn:   resumeApi.getLatest,
    // A locally persisted résumé wins — no need to hit the server at all
    // (same guard the old App.tsx effect had).
    enabled:   enabled && !hasLocal,
    staleTime: Infinity,
  })

  // Mirror into the store — only non-empty text, matching the old effect.
  useEffect(() => {
    if (data?.text?.trim()) setResume(data.text)
  }, [data, setResume])

  // Ready when a local copy exists, or the one fetch settled (success OR
  // error) — Compose gates on this so it doesn't flash "no resume" mid-flight.
  return { resumeReady: hasLocal || isFetched }
}
