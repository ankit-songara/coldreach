/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Google OAuth 2.0 Web client ID for "Sign in with Google". Empty → button hidden. */
  readonly VITE_GOOGLE_CLIENT_ID?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
