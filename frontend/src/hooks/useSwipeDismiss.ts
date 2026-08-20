import { useEffect, useRef, type RefObject } from 'react'

/**
 * Touch swipe-to-dismiss for the right-side drawer (Apple "Designing Fluid
 * Interfaces": §2 direct manipulation, §5 velocity handoff, §6 momentum
 * projection, §9 rubber-banding, §10 axis intent, §3 interruptibility).
 *
 * Touch only — desktop keeps scrim/✕/Escape (and a mouse drag would fight
 * text selection in the notes field). Dependency-free: 1:1 tracking is
 * imperative on the element; the release settle/fling is a GPU transform
 * transition whose duration scales with the release velocity, which
 * approximates handing the gesture's momentum to the animation.
 *
 * onDismiss (the raw unmount) fires only after the panel has flung fully
 * off-screen, so the exit continues from the finger instead of snapping to a
 * canned keyframe.
 */
export function useSwipeDismiss({
  panel, scrim, onDismiss,
}: {
  panel: RefObject<HTMLElement | null>
  scrim: RefObject<HTMLElement | null>
  onDismiss: () => void
}) {
  // onDismiss is a fresh inline arrow from the parent every render, so keying
  // the gesture effect on it tore down and rebuilt the pointer listeners on
  // ANY parent re-render — including MID-FLING (a background contacts refetch
  // is enough), which cancelled the fling's unmount timeout and left the panel
  // flung off-screen but still mounted (body scroll stuck locked). Hold it in a
  // ref and depend only on the stable panel/scrim refs, so the listeners and
  // any in-flight settle/fling are set up once and never interrupted.
  const onDismissRef = useRef(onDismiss)
  onDismissRef.current = onDismiss

  useEffect(() => {
    const el = panel.current
    if (!el) return

    const AXIS_THRESHOLD = 8          // px of intent before we commit to an axis
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

    let width = el.getBoundingClientRect().width || 380
    let startX = 0     // clientX that maps to translateX 0 (press point minus resume offset)
    let pressX = 0, pressY = 0   // raw press point, for axis-intent detection
    let curX = 0
    let pointerId = -1
    let tracking = false      // pointer is down, axis not yet decided
    let dragging = false      // committed to a horizontal drag
    const hist: Array<{ x: number; t: number }> = []
    let cancelPending: (() => void) | null = null   // clears an in-flight settle/fling transition

    el.style.touchAction = 'pan-y'   // browser keeps vertical scroll; we own horizontal

    const currentTranslateX = (): number => {
      try {
        const m = new DOMMatrixReadOnly(getComputedStyle(el).transform)
        return m.m41
      } catch { return curX }
    }

    // Resist a leftward (past-the-open-edge) drag instead of following 1:1 (§9).
    const rubberband = (over: number): number => {
      const c = 0.55
      return (over * width * c) / (width + c * Math.abs(over))
    }

    const paint = (x: number) => {
      curX = x
      el.style.transform = `translateX(${x}px)`
      if (scrim.current) scrim.current.style.opacity = String(Math.max(0, 1 - x / width))
    }

    // §6 projection: where momentum would carry the panel (scroll-decel form).
    const project = (velocity: number, decel = 0.998) =>
      (velocity / 1000) * decel / (1 - decel)

    // Release velocity from the MOST RECENT motion (§5) — so a finger that
    // paused before lifting reads ~0 and settles, not dismisses. Window back
    // ~100ms; a span too small to measure (e.g. events in one tick) is
    // untrustworthy and treated as no throw.
    const releaseVelocity = (): number => {
      if (hist.length < 2) return 0
      const last = hist[hist.length - 1]
      let ref = hist[hist.length - 2]
      for (let i = hist.length - 2; i >= 0; i--) {
        if (last.t - hist[i].t > 100) break
        ref = hist[i]
      }
      const dt = last.t - ref.t
      if (dt < 5) return 0
      const v = ((last.x - ref.x) / dt) * 1000       // px/s
      return Math.max(-5000, Math.min(5000, v))       // clamp pathological values
    }

    // One-shot transition end: fires the callback once, via transitionend OR a
    // timeout fallback (transitionend can be missed if the tab isn't
    // compositing, or under reduced motion where duration is ~0).
    const afterTransition = (durationMs: number, cb: () => void) => {
      let done = false
      const finish = () => {
        if (done) return
        done = true
        el.removeEventListener('transitionend', onEnd)
        clearTimeout(timer)
        cancelPending = null
        cb()
      }
      const onEnd = (e: TransitionEvent) => { if (e.target === el && e.propertyName === 'transform') finish() }
      el.addEventListener('transitionend', onEnd)
      const timer = setTimeout(finish, durationMs + 60)
      cancelPending = () => {
        done = true
        el.removeEventListener('transitionend', onEnd)
        clearTimeout(timer)
        cancelPending = null
      }
    }

    const settleBack = () => {
      if (reduce) { el.style.transition = ''; el.style.transform = ''; if (scrim.current) scrim.current.style.opacity = '1'; return }
      el.style.transition = 'transform .32s var(--ease-spring)'
      el.style.transform = 'translateX(0px)'
      if (scrim.current) { scrim.current.style.transition = 'opacity .32s var(--ease-out)'; scrim.current.style.opacity = '1' }
      afterTransition(320, () => { el.style.transition = ''; el.style.transform = '' })
      curX = 0
    }

    const flingOut = (velocity: number) => {
      if (reduce) { onDismissRef.current(); return }
      const remaining = Math.max(1, width - curX)
      // Velocity handoff (§5), approximated: faster flick → shorter travel time.
      const durS = Math.min(0.4, Math.max(0.12, remaining / Math.max(700, Math.abs(velocity))))
      el.style.transition = `transform ${durS}s var(--ease-out)`
      el.style.transform = `translateX(${width}px)`
      if (scrim.current) { scrim.current.style.transition = `opacity ${durS}s var(--ease-out)`; scrim.current.style.opacity = '0' }
      afterTransition(durS * 1000, () => onDismissRef.current())
    }

    const onDown = (e: PointerEvent) => {
      if (e.pointerType !== 'touch') return
      // Ignore a second finger while a gesture is live — it would hijack
      // pointerId and clear `dragging`, stranding the first finger's release
      // (its onUp is then filtered out and the panel never settles/dismisses).
      if (tracking || dragging) return
      // Record only — don't disturb the entrance keyframe. A pure tap must
      // leave the drawer's open animation and any button clicks untouched.
      width = el.getBoundingClientRect().width || width
      pressX = e.clientX
      pressY = e.clientY
      hist.length = 0
      hist.push({ x: e.clientX, t: performance.now() })
      pointerId = e.pointerId
      tracking = true
      dragging = false
    }

    const onMove = (e: PointerEvent) => {
      if (e.pointerId !== pointerId) return
      if (!tracking && !dragging) return
      if (!dragging) {
        const movedX = e.clientX - pressX
        const movedY = e.clientY - pressY
        if (Math.abs(movedX) < AXIS_THRESHOLD && Math.abs(movedY) < AXIS_THRESHOLD) return
        if (Math.abs(movedY) > Math.abs(movedX)) { tracking = false; return }   // vertical → let it scroll
        // Commit to a horizontal drag. §3 interruptibility: take over from
        // wherever the panel visually is (mid-entrance/settle/fling), not a
        // logical target — and stop the CSS animation/transition from
        // overriding our imperative transform/opacity in the cascade.
        dragging = true
        tracking = false
        cancelPending?.()
        const live = currentTranslateX()
        el.style.animation = 'none'
        el.style.transition = 'none'
        startX = e.clientX - live       // translateX = clientX - startX resumes at `live`
        curX = live
        if (scrim.current) { scrim.current.style.animation = 'none'; scrim.current.style.transition = 'none' }
        try { el.setPointerCapture(pointerId) } catch { /* ignore */ }
      }
      hist.push({ x: e.clientX, t: performance.now() })
      if (hist.length > 6) hist.shift()
      const x = e.clientX - startX                    // 1:1 with the finger (grab offset preserved)
      paint(x >= 0 ? x : rubberband(x))
    }

    const onUp = (e: PointerEvent) => {
      if (e.pointerId !== pointerId) return
      const wasDragging = dragging
      tracking = false
      dragging = false
      pointerId = -1
      if (!wasDragging) return                       // a tap — let the click through
      const v = releaseVelocity()
      const projected = curX + project(v)
      // Dismiss when momentum projects past the halfway line, or on a decisive
      // rightward flick; otherwise spring back to open.
      if (projected > width * 0.5 || v > 500) flingOut(v)
      else settleBack()
    }

    el.addEventListener('pointerdown', onDown)
    el.addEventListener('pointermove', onMove)
    el.addEventListener('pointerup', onUp)
    el.addEventListener('pointercancel', onUp)
    return () => {
      el.removeEventListener('pointerdown', onDown)
      el.removeEventListener('pointermove', onMove)
      el.removeEventListener('pointerup', onUp)
      el.removeEventListener('pointercancel', onUp)
      cancelPending?.()
    }
  }, [panel, scrim])
}
