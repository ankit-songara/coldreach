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

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str   = "sqlite:///./coldreach.db"

    # ── LLM — provider-agnostic ──────────────────────────────────────────────
    llm_provider: str   = "auto"          # auto | ollama | groq | openai | openrouter
    llm_model: str      = ""              # empty → use provider default
    llm_api_key: str    = ""              # groq / openai / openrouter key
    llm_temperature: float = 0.7

    # Provider-specific defaults
    ollama_base_url: str       = "http://localhost:11434"
    ollama_default_model: str  = "llama3.1"
    groq_default_model: str    = "llama-3.1-8b-instant"

    # ── Optional enrichment ──────────────────────────────────────────────────
    hunter_api_key: str = ""    # hunter.io — domain email search
    github_token: str   = ""    # 60 → 5,000 GitHub req/hr

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]


settings = Settings()
