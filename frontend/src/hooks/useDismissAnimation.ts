import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Lets an overlay play an EXIT animation before the parent unmounts it.
 *
 * Apple §7 (spatial consistency): "if something disappears one way, we expect
 * it to emerge from where it came." Our overlays all mounted with an entrance
 * and then vanished — this closes the seam. All of them already funnel every
 * close (Escape, scrim, ✕) through one callback, so the component calls
 * `dismiss()` instead of the raw `onClose`: we flip `closing` true (the
 * component swaps to its reverse keyframe + fades the scrim), then invoke the
 * real `onClose` once the animation would have finished.
 *
 * Reduced motion: the global CSS zeroes animation duration, so we also skip the
 * timer wait — no dead delay before the unmount.
 */
// IMPORTANT: this hook latches `closing` true on dismiss and never resets it,
// so it is only safe for CONDITIONALLY-mounted overlays (a fresh instance per
// open). A persistently-mounted overlay that toggles an `open` prop would
// reopen still latched — mount it conditionally instead (App mounts
// CommandPalette as `{open && <CommandPalette .../>}`).
export function useDismissAnimation(
  onClose: () => void,
  durationMs = 200,
): { closing: boolean; dismiss: () => void } {
  const [closing, setClosing] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const closingRef = useRef(false)

  const dismiss = useCallback(() => {
    if (closingRef.current) return   // a close is already in flight — ignore repeats
    closingRef.current = true
    setClosing(true)
    const reduce =
      typeof window !== 'undefined' &&
      window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    timer.current = setTimeout(onClose, reduce ? 0 : durationMs)
  }, [onClose, durationMs])

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])

  return { closing, dismiss }
}
