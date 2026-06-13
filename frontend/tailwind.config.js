/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        mono: ['"JetBrains Mono"', 'monospace'],
        sans: ['"DM Sans"', 'system-ui', 'sans-serif'],
      },
      colors: {
        surface: { DEFAULT: '#06080f', 1: '#0b0f1a', 2: '#0f1929' },
        border:  { DEFAULT: '#1a2535', dim: '#0d1829' },
        accent:  { DEFAULT: '#22d3ee', dim: 'rgba(34,211,238,0.12)' },
      },
    },
  },
  plugins: [],
}
