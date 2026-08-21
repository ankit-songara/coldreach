"""
Application configuration via environment variables.
All settings have sensible defaults for local development.

LLM auto-detection logic:
  LLM_PROVIDER=auto (default)
    → tries Ollama first  (localhost:11434)
    → falls back to Groq  (needs GROQ_API_KEY)

Force a provider:
  LLM_PROVIDER=ollama   LLM_MODEL=llama3.1
  LLM_PROVIDER=groq     LLM_API_KEY=gsk_xxx
  LLM_PROVIDER=openai   LLM_API_KEY=sk-xxx
"""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── App ──────────────────────────────────────────────────────────────────
    app_name: str       = "ColdReach"
    app_version: str    = "1.0.0"
    debug: bool         = False
    cors_origins: str   = "http://localhost:5173"   # Vite dev server

    # Vercel serves the frontend project from stable aliases AND a unique
    # per-deployment URL on every deploy — a static allowlist always eventually
    # misses one, and a blocked CORS request looks identical to "server
    # unreachable" in the browser. This regex covers every URL shape Vercel
    # generates for the frontend project.
    #
    # Scoped to `coldreach` + any hyphenated suffix on vercel.app, so the
    # frontend project can be RENAMED to any clean `coldreach-…` name
    # (coldreach-app, coldreach-hq, …) without touching this — the new domain
    # keeps matching. Anchored at both ends (^…$) so look-alikes are still
    # rejected: evil-coldreach.vercel.app (wrong prefix), coldreach.vercel.app.evil.com
    # (wrong suffix), coldreach.vercelapp.com (not vercel.app). Auth is Bearer-token,
    # not cookie, so CORS breadth here isn't a credential-exposure vector.
    # Override via CORS_ORIGIN_REGEX for a custom domain.
    cors_origin_regex: str = r"^https://coldreach(-[a-z0-9]+)*\.vercel\.app$"

    # ── Auth ─────────────────────────────────────────────────────────────────
    # Google OAuth 2.0 Web client ID for "Sign in with Google". Empty → the
    # Google endpoint returns 503 and the frontend hides the button; email/
    # password login is unaffected. Create one at console.cloud.google.com.
    google_client_id: str = ""

    # ── Gmail OAuth (one-click "Connect Gmail" — no App Password) ────────────
    # Client SECRET of the same OAuth client as google_client_id. Empty → the
    # OAuth connect flow returns 503 and the frontend shows only the App
    # Password path. Requires gmail.send + gmail.readonly scopes on the
    # consent screen and backend_public_url/…/callback as a redirect URI.
    google_client_secret: str = ""
    # Public base URL of THIS backend (no trailing slash) — used to build the
    # OAuth redirect_uri Google sends the browser back to.
    backend_public_url: str = "http://localhost:8000"
    # Where to land the browser after the OAuth callback stores the grant.
    frontend_url: str = "http://localhost:5173"

    # ── Database ─────────────────────────────────────────────────────────────
    # Lives under data/ alongside the encryption key (matches .env.example and
    # the Docker volume mount). The directory is auto-created on startup.
    database_url: str   = "sqlite:///./data/coldreach.db"

    # ── LLM — provider-agnostic ──────────────────────────────────────────────
    llm_provider: str   = "auto"          # auto | ollama | groq | openai | openrouter
    llm_model: str      = ""              # empty → use provider default
    # Accepts GROQ_API_KEY as an alias: the "set GROQ_API_KEY" guidance in the
    # no-provider error (and Groq's own docs) pointed at a var the app never
    # actually read — operators followed it and still got the error.
    llm_api_key: str    = Field("", validation_alias=AliasChoices("LLM_API_KEY", "GROQ_API_KEY"))
    llm_temperature: float = 0.7
    # Cap output tokens: a cold email (subject + ≤120-word body) is ~250 tokens,
    # so 768 is ample headroom while bounding worst-case generation time AND the
    # per-request token spend that counts against Groq's free-tier tokens/minute
    # limit — the ceiling that was 429-ing rapid "Generate all" bursts.
    llm_max_tokens: int = 768

    # Provider-specific defaults
    ollama_base_url: str       = "http://localhost:11434"
    ollama_default_model: str  = "llama3.1"
    # 70B writes noticeably better cold emails than 8B (follows the "no invented
    # facts" rules, stronger hooks). Still on Groq's free tier — just slower per
    # request, which the sequential compose flow absorbs fine.
    # Fast production model (~1000 tok/s) — fits the serverless wall and the
    # free-tier rate limits far better than the 70b. See factory._GROQ_FALLBACKS.
    groq_default_model: str    = "openai/gpt-oss-20b"

    # ── Optional enrichment ──────────────────────────────────────────────────
    hunter_api_key: str = ""    # hunter.io — domain email search
    github_token: str   = ""    # 60 → 5,000 GitHub req/hr

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]


settings = Settings()
