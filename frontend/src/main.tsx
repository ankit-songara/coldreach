import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { GoogleOAuthProvider } from '@react-oauth/google'
import { Toaster } from 'react-hot-toast'
import App from './App'
import './styles/index.css'

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
})

// Empty string is fine: with no client ID the Auth screen hides the Google
// button and falls back to email/password. The provider is harmless when unused.
const googleClientId = import.meta.env.VITE_GOOGLE_CLIENT_ID ?? ''

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <GoogleOAuthProvider clientId={googleClientId}>
      <QueryClientProvider client={queryClient}>
        <App />
        <Toaster position="bottom-right" toastOptions={{
          style: { background: '#0b0f1a', color: '#e2e8f0', border: '1px solid #1a2535' },
        }} />
      </QueryClientProvider>
    </GoogleOAuthProvider>
  </React.StrictMode>,
)
