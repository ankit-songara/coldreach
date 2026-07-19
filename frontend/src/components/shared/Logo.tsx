// Brand mark: "The Dart" (direction 1a) — a paper dart mid-flight on the
// coral tile — with the restrained single-ink wordmark from direction 1b.
// Pure CSS geometry, so it inherits theme tokens and scales crisply.

export default function Logo({ size = 26, wordmark = false }: { size?: number; wordmark?: boolean }) {
  // Proportions from the design spec (76px reference tile).
  const dart = size * 0.37
  const echo = size * 0.17
  const tile = (
    <span
      aria-hidden
      style={{
        width: size, height: size,
        borderRadius: size * 0.28,
        background: 'var(--accent)',
        position: 'relative', overflow: 'hidden',
        display: 'inline-block', flexShrink: 0,
      }}
    >
      <span style={{
        position: 'absolute', top: size * 0.315, left: size * 0.315,
        width: dart, height: dart,
        background: 'var(--on-accent)',
        transform: 'rotate(45deg)', borderRadius: Math.max(1, size * 0.05),
      }} />
      <span style={{
        position: 'absolute', top: size * 0.58, left: size * 0.18,
        width: echo, height: echo,
        background: 'var(--on-accent)', opacity: 0.45,
        transform: 'rotate(45deg)', borderRadius: Math.max(1, size * 0.026),
      }} />
    </span>
  )

  if (!wordmark) return tile

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: size * 0.31 }}>
      {tile}
      <span style={{
        fontSize: size * 0.65, fontWeight: 800, letterSpacing: '-0.03em',
        color: 'var(--text)', fontFamily: 'var(--font-display)', lineHeight: 1,
      }}>
        ColdReach
      </span>
    </span>
  )
}
