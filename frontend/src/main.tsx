import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { GoogleOAuthProvider } from '@react-oauth/google'
import { Toaster } from 'react-hot-toast'
import App from './App'
import ErrorBoundary from './components/shared/ErrorBoundary'
import { queryClient } from './lib/queryClient'
import { initTheme } from './lib/theme'
import './styles/index.css'

initTheme()

// Empty string is fine: with no client ID the Auth screen hides the Google
// button and falls back to email/password. The provider is harmless when unused.
const googleClientId = import.meta.env.VITE_GOOGLE_CLIENT_ID ?? ''

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
    <GoogleOAuthProvider clientId={googleClientId}>
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
      </QueryClientProvider>
    </GoogleOAuthProvider>
    </ErrorBoundary>
  </React.StrictMode>,
)
