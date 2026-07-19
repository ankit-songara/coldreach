// Brand mark — Logo Kit V1 "They're typing": three typing-indicator dots on
// the tile — a reply being typed, the moment every job-seeker is waiting for.
//
// Geometry (everything scales from tile width W): radius .26W · dot .14W ·
// gap .07W · static dot opacities .40/.70/1.00 (implying the motion).
// Colors are the kit's four brand constants via --logo-tile/--logo-dot
// (light: ink tile + bright coral; dark: cream tile + coral) — never
// theme-accent tokens, and never a coral background behind the tile.
//
// `animated` is for loading/waiting surfaces and the app nav mark ONLY
// (kit rule 04/06): static everywhere else. The base opacity stays the
// static fade even when animating, so prefers-reduced-motion (which kills
// the animation globally) falls back to the correct static mark.

const STATIC_FADE = [0.4, 0.7, 1] as const

// Brand constant from the kit ("Coral — dots on cream, ↗"): the wordmark
// arrow is #e2603f in BOTH themes.
const ARROW_CORAL = '#e2603f'

export default function Logo({ size = 30, wordmark = false, animated = false }: {
  size?: number
  wordmark?: boolean
  animated?: boolean
}) {
  const dot = (i: number) => ({
    width: size * 0.14, height: size * 0.14, borderRadius: '50%',
    background: 'var(--logo-dot)',
    opacity: STATIC_FADE[i],
    animation: animated ? `crDot 1.2s ${i * 0.15}s ease-in-out infinite` : undefined,
  })

  const tile = (
    <span
      aria-hidden
      style={{
        width: size, height: size,
        borderRadius: size * 0.26,
        background: 'var(--logo-tile)',
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        gap: size * 0.07, flexShrink: 0,
      }}
    >
      <span style={dot(0)} />
      <span style={dot(1)} />
      <span style={dot(2)} />
    </span>
  )

  if (!wordmark) return tile

  // Lockup: lowercase, weight 800, tracking −0.04em, coral ↗ between the words.
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: size * 0.31 }}>
      {tile}
      <span style={{
        fontFamily: 'var(--font-display)',
        fontSize: Math.round(size * 0.64), fontWeight: 800, lineHeight: 1,
        letterSpacing: '-0.04em', color: 'var(--text)', whiteSpace: 'nowrap',
      }}>
        cold<span style={{ color: ARROW_CORAL, fontSize: '.8em', verticalAlign: '.18em' }}>↗</span>reach
      </span>
    </span>
  )
}
