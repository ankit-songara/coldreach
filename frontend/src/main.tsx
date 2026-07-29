import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { GoogleOAuthProvider } from '@react-oauth/google'
import { Toaster } from 'react-hot-toast'
import { Analytics } from '@vercel/analytics/react'
import { SpeedInsights } from '@vercel/speed-insights/react'
import App from './App'
import ErrorBoundary from './components/shared/ErrorBoundary'
import { queryClient } from './lib/queryClient'
import { initTheme } from './lib/theme'
import './styles/index.css'

initTheme()

// With no client ID the Auth screen hides the Google button (GOOGLE_ENABLED)
// and falls back to email/password — so the provider isn't needed. It is NOT
// harmless when unused: GoogleOAuthProvider injects the third-party GSI
// <script> on mount regardless of clientId, on EVERY visit (incl. the logged-out
// landing). Mount it only when Google sign-in is actually configured.
const googleClientId = import.meta.env.VITE_GOOGLE_CLIENT_ID ?? ''

const app = (
  <QueryClientProvider client={queryClient}>
    <App />
    <Toaster
      position="bottom-right"
      // On phones the fixed bottom tab bar would cover toasts — the CSS
      // var lifts them above it (see index.css media query).
      containerStyle={{ bottom: 'var(--toast-bottom, 16px)' }}
      toastOptions={{
      // Match the app's warm light theme instead of a stock dark toast.
      style: {
        background: 'var(--surface-1)',
        color: 'var(--text)',
        border: '1px solid var(--border-strong)',
        boxShadow: 'var(--shadow-md)',
      },
    }} />
    <Analytics />
    <SpeedInsights />
  </QueryClientProvider>
)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      {googleClientId
        ? <GoogleOAuthProvider clientId={googleClientId}>{app}</GoogleOAuthProvider>
        : app}
    </ErrorBoundary>
  </React.StrictMode>,
)
