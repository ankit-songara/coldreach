/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        mono:    ['"IBM Plex Mono"', 'ui-monospace', 'monospace'],
        sans:    ['"Plus Jakarta Sans"', 'system-ui', 'sans-serif'],
        display: ['"Plus Jakarta Sans"', 'system-ui', 'sans-serif'],
      },
      // Utilities resolve to the CSS variables so dark mode flips them for
      // free — hardcoded hexes here would silently bake the light palette
      // into every hover:border-accent etc.
      colors: {
        surface: { DEFAULT: 'var(--bg)', 1: 'var(--surface-1)', 2: 'var(--surface-2)', 3: 'var(--surface-3)' },
        border:  { DEFAULT: 'var(--border)', dim: 'var(--border-dim)', strong: 'var(--border-strong)' },
        accent:  { DEFAULT: 'var(--accent)', dim: 'var(--accent-dim)', tint: 'var(--accent-tint)' },
      },
      boxShadow: {
        xs: 'var(--shadow-xs)',
        sm: 'var(--shadow-sm)',
        md: 'var(--shadow-md)',
        lg: 'var(--shadow-lg)',
      },
    },
  },
  plugins: [],
}
