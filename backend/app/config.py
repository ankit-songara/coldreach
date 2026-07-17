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

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── App ──────────────────────────────────────────────────────────────────
    app_name: str       = "ColdReach"
    app_version: str    = "1.0.0"
    debug: bool         = False
    cors_origins: str   = "http://localhost:5173"   # Vite dev server

    # Vercel gives a project BOTH stable aliases (coldreach-niyp.vercel.app,
    # coldreach-niyp-<team>.vercel.app, coldreach-niyp-git-<branch>-<team>.vercel.app)
    # AND a brand-new unique per-deployment URL on every single deploy
    # (coldreach-niyp-<hash>-<team>.vercel.app). A static comma-separated
    # allowlist (cors_origins above) can only ever cover the aliases known at
    # the time it was set — it will always eventually miss a valid, real
    # deployment URL, and a blocked CORS request is indistinguishable from
    # "server unreachable" to the browser (confirmed live: coldreach-niyp.vercel.app
    # got a clean 200 preflight while coldreach-niyp-cold-reach.vercel.app and
    # coldreach-niyp-git-master-cold-reach.vercel.app — same frontend project,
    # different URL — got 400s). A regex matching every URL Vercel generates
    # FOR THIS PROJECT closes that gap permanently instead of chasing it one
    # missing origin at a time. Override via CORS_ORIGIN_REGEX if the Vercel
    # project name ever changes.
    cors_origin_regex: str = r"^https://coldreach-niyp(-[a-z0-9]+)*\.vercel\.app$"

    # ── Auth ─────────────────────────────────────────────────────────────────
    # Google OAuth 2.0 Web client ID for "Sign in with Google". Empty → the
    # Google endpoint returns 503 and the frontend hides the button; email/
    # password login is unaffected. Create one at console.cloud.google.com.
    google_client_id: str = ""

    # ── Database ─────────────────────────────────────────────────────────────
    # Lives under data/ alongside the encryption key (matches .env.example and
    # the Docker volume mount). The directory is auto-created on startup.
    database_url: str   = "sqlite:///./data/coldreach.db"

    # ── LLM — provider-agnostic ──────────────────────────────────────────────
    llm_provider: str   = "auto"          # auto | ollama | groq | openai | openrouter
    llm_model: str      = ""              # empty → use provider default
    llm_api_key: str    = ""              # groq / openai / openrouter key
    llm_temperature: float = 0.7

    # Provider-specific defaults
    ollama_base_url: str       = "http://localhost:11434"
    ollama_default_model: str  = "llama3.1"
    # 70B writes noticeably better cold emails than 8B (follows the "no invented
    # facts" rules, stronger hooks). Still on Groq's free tier — just slower per
    # request, which the sequential compose flow absorbs fine.
    groq_default_model: str    = "llama-3.3-70b-versatile"

    # ── Optional enrichment ──────────────────────────────────────────────────
    hunter_api_key: str = ""    # hunter.io — domain email search
    github_token: str   = ""    # 60 → 5,000 GitHub req/hr

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]


settings = Settings()
