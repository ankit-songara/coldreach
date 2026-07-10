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
  document.documentElement.setAttribute('data-theme', resolveTheme(theme))
  localStorage.setItem(STORAGE_KEY, theme)
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
