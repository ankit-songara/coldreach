/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Google OAuth 2.0 Web client ID for "Sign in with Google". Empty → button hidden. */
  readonly VITE_GOOGLE_CLIENT_ID?: string
  /**
   * Backend API base URL including /api suffix, e.g. https://coldreach-api.vercel.app/api
   * Leave unset in development — Vite proxy handles /api → localhost:8000.
   */
  readonly VITE_API_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
