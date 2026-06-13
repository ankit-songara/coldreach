# Architecture

## Design Patterns

### Strategy Pattern — Scrapers
Each scraper (HN, GitHub, Web) implements `BaseScraper`. The hunt endpoint composes them and runs in parallel. Adding a new source = one new file + one line registration.

```
BaseScraper (ABC)
  ├── HackerNewsScraper   HN Algolia free API
  ├── GitHubScraper       org commit emails
  ├── WebScraper          Playwright headless
  └── HunterEnricher      Hunter.io (optional)
```

### Factory Pattern — LLM Provider
`llm/factory.py` creates the correct LangChain `BaseChatModel` based on `LLM_PROVIDER` env var. Caller never knows which provider is active.

### Repository Pattern — Database
All DB access goes through `ContactRepository`, `DraftRepository`, `ResumeRepository`. Routes never touch SQLAlchemy directly.

### Dependency Injection — FastAPI
Database sessions and config are injected via `Depends()`. Tests can substitute fakes cleanly.

## Request Flow

```
POST /api/hunt
  → api/hunt.py
    → build_scrapers()           # Strategy: pick sources
    → asyncio.gather(scrapers)   # parallel execution
    → dedupe by email
    → ContactRepository.bulk_create()
    → return HuntResult

POST /api/compose
  → api/compose.py
    → ContactRepository.get_by_id()
    → EmailGenerator.generate()   # Factory: uses detected LLM
    → DraftRepository.create()
    → return DraftOut
```

## LLM Auto-Detection

```
startup:
  detect_provider()
    try: GET localhost:11434/api/tags   → Ollama ✓
    except:
      if LLM_API_KEY (Groq): → Groq ✓
      else: raise RuntimeError with a helpful message
```

`auto` never falls back to the `mock` provider. The mock (deterministic
placeholder copy, never meant to be sent) is reachable only via an explicit
`LLM_PROVIDER=mock`, for demos and CI.

## Authentication & Sessions

Email/password accounts; passwords are PBKDF2-HMAC-SHA256. Session tokens are
Fernet-encrypted payloads `{uid, ver, exp}` with a 30-day TTL. `ver` is the
user's `token_version`; logout (and any future password change) bumps it,
invalidating every previously-issued token. Login is rate-limited per IP.

## Background Scheduler

A single asyncio task polls `scheduled_emails` every 60s, sending due first-touch
and follow-up messages per user, respecting each user's daily cap and skipping
contacts that have replied or whose address is known invalid/bounced. Inbox sync
cancels pending follow-ups when a reply is detected.
