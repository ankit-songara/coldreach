export type Theme = 'light' | 'dark' | 'system'

const STORAGE_KEY = 'coldreach-theme'

export function getStoredTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (stored === 'light' || stored === 'dark' || stored === 'system') return stored
  return 'system'
}

export function resolveTheme(theme: Theme): 'light' | 'dark' {
  if (theme !== 'system') return theme
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function applyTheme(theme: Theme) {
  const resolved = resolveTheme(theme)
  document.documentElement.setAttribute('data-theme', resolved)
  localStorage.setItem(STORAGE_KEY, theme)
  // Keep the browser chrome (Android address bar, Safari toolbar) matched to
  // the app canvas. The inline script in index.html sets the same values
  // pre-paint; these hexes are --bg light/dark.
  document.querySelector('meta[name="theme-color"]')
    ?.setAttribute('content', resolved === 'dark' ? '#151210' : '#faf7f2')
}

export function cycleTheme(): Theme {
  const current = getStoredTheme()
  const next: Theme = current === 'light' ? 'dark' : current === 'dark' ? 'system' : 'light'
  applyTheme(next)
  return next
}

export function initTheme() {
  applyTheme(getStoredTheme())
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (getStoredTheme() === 'system') applyTheme('system')
  })
}
