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
      colors: {
        surface: { DEFAULT: '#faf7f2', 1: '#ffffff', 2: '#f5efe6', 3: '#efe7d9' },
        border:  { DEFAULT: '#eae1d3', dim: '#f2ece2', strong: '#ddd1bf' },
        accent:  { DEFAULT: '#e2603f', dim: 'rgba(226,96,63,0.10)', tint: '#fbede8' },
      },
      boxShadow: {
        xs: '0 1px 2px rgba(40,30,20,0.05)',
        sm: '0 1px 2px rgba(40,30,20,0.04), 0 3px 8px -3px rgba(40,30,20,0.08)',
        md: '0 2px 4px rgba(40,30,20,0.04), 0 14px 30px -10px rgba(40,30,20,0.12)',
        lg: '0 30px 60px -18px rgba(40,30,20,0.22)',
      },
    },
  },
  plugins: [],
}
