# Architecture

ColdReach is a five-stage pipeline wrapped in a FastAPI backend and a React SPA. This doc covers the domains, the design patterns that hold them together, and the runtime constraints that shape them. For day-to-day agent guidance see [AGENTS.md](../AGENTS.md); for setup see [SETUP.md](../SETUP.md).

## The five domains

```
  HUNT ──▶ RESOLVE & VERIFY ──▶ COMPOSE ──▶ SEND ──▶ TRACK
 sources   real email +          LLM email   Gmail    IMAP replies
           confidence 0–100      per role    paced    + follow-ups
```

| Domain | Entry point | Core modules | Responsibility |
|--------|-------------|--------------|----------------|
| **Hunt** | `api/hunt.py` | `scrapers/*` | Discover people & companies hiring now, from ~21 public sources, in parallel. |
| **Resolve & Verify** | `scrapers/resolver.py`, `verifier.py` | `scrapers/web.py`, `enricher.py`, `netguard.py` | Turn name+company into a real, deliverable address with a confidence score. Never fabricates. |
| **Compose** | `api/compose.py` | `llm/*` | Generate a role-aware cold email grounded in the résumé and hunt-time context. |
| **Send** | `api/send.py` | `mailer.py`, `gmail_oauth.py` | Send from the user's own Gmail, paced/capped/dedup-guarded. |
| **Track** | `api/inbox.py`, `api/analytics.py` | `mailer.py` (IMAP), `db/*` | Detect replies/bounces, cancel follow-ups on reply, compute the funnel. |

## Design patterns

### Strategy — Scrapers
Every source implements `BaseScraper` (ATS sources extend `BaseATSScraper`). `api/hunt.py::_build_scrapers()` composes them into a list and runs them with `asyncio.gather`, then dedupes by email. **Adding a source = one class + one line in the list.** No source can break the hunt: individual scraper failures are caught and logged, not propagated.

```
BaseScraper (ABC)
 ├── BaseATSScraper → Greenhouse, Lever, Ashby, SmartRecruiters,
 │                    Recruitee, Workable, Breezy, Teamtailor
 ├── WorkdayScraper
 ├── job boards      → RemoteOK, Remotive, Arbeitnow, Jobicy, Himalayas,
 │                     TheMuse, WeWorkRemotely, WorkingNomads
 ├── keyword search  → Workable search, SmartRecruiters search
 ├── HackerNewsScraper (HN "Who is Hiring")
 ├── YCStartupsScraper (pool — fills leftover funnel slots, tagged _pool)
 └── HunterEnricher (optional, keyed)
```

### Factory — LLM provider
`llm/factory.py` builds the right LangChain `BaseChatModel` from `LLM_PROVIDER`. It resolves the provider **once and caches it**. For Groq it exposes a **candidate chain**: `groq_model_candidates()` drops known-retired models (`_RETIRED_MODELS`) and appends `_GROQ_FALLBACKS`, so a decommissioned model auto-advances to the next instead of hard-failing. Callers never know which provider/model is live.

### Repository — Database
All DB access goes through repositories (`ContactRepository`, `DraftRepository`, `ResumeRepository`, …) in `db/crud.py`. Routes never touch SQLAlchemy sessions directly. Makes user-scoping and testing uniform.

### Dependency injection — FastAPI
DB sessions, the current user, and config are injected via `Depends()` (`deps.py`). Tests substitute fakes cleanly; auth is enforced in one place.

## Request flows

```
POST /api/hunt
  → api/hunt.py
    → _build_scrapers()            # Strategy: pick sources
    → asyncio.gather(scrapers)     # parallel; per-scraper failures swallowed
    → resolve + verify addresses   # confidence 0–100, invalid flagged
    → dedupe, skip already-owned leads
    → ContactRepository.bulk_create()
    → return HuntResult

POST /api/compose
  → api/compose.py
    → ContactRepository.get_by_id()          # user-scoped
    → EmailGenerator.generate()              # Factory LLM, 2-pass quality loop
    │     ├─ time budget: skip regen if draft-1 neared the wall
    │     └─ candidate-chain fallback on model_not_found
    → DraftRepository.create()
    → 200 draft | 429 rate-limited | 502 other LLM error   (honest status)
```

## LLM auto-detection

```
startup / first compose:
  detect_provider()
    try GET localhost:11434/api/tags  → Ollama ✓
    else if LLM_API_KEY set            → Groq ✓
    else                               → clear RuntimeError (what to configure)
```

`auto` **never** falls back to the `mock` provider. The mock (deterministic, unsendable placeholder) is reachable only via explicit `LLM_PROVIDER=mock`, for demos and CI.

## Compose under the serverless wall

Vercel kills a function at **60s**. The generator is built around that:
- Output is capped (`llm_max_tokens`, default 768) — a cold email is ~250 tokens, so this is headroom that also bounds worst-case latency and per-request Groq token spend.
- The two-pass quality loop has a **time budget**: if the first draft already spent most of the wall, the regeneration pass is skipped and the usable draft is returned.
- **429s are never retried.** Retrying into a throttled free-tier window triples load and makes bursts worse; the error propagates as an honest `429` so the UI can say "rate-limited, try again" rather than hanging until timeout.

## Authentication & sessions

Email/password (PBKDF2-HMAC-SHA256) plus optional "Sign in with Google" (ID-token verification). Session tokens are Fernet-encrypted `{uid, ver, exp}` payloads with a 30-day TTL. `ver` is the user's `token_version`; logout bumps it, invalidating every previously-issued token. Login is rate-limited per IP. Auth is Bearer-token, not cookie.

## Data persistence

SQLAlchemy over SQLite (local default) or Postgres (Supabase, hosted). On serverless, connections use **NullPool** — each request is a fresh TLS connection, so the code minimizes round trips and avoids N+1s. `db/migrations.py` applies lightweight, idempotent DDL on startup; irreversible changes to shared prod data require explicit human sign-off.

## Background jobs (outside the request path)

Serverless request handlers die at the 60s wall, and there's no always-on worker,
so several tables note "no cron on serverless" and expire lazily at read time.
Work that genuinely needs to run long, uncapped, and unattended lives in
`app/jobs/` and is triggered by **GitHub Actions cron** — not Vercel:

- **`discover_companies.py`** (daily) harvests the HN "Who is Hiring" thread's
  verified apply-links into the shared `KnownCompany` directory
  (`source="discovered"`), so every user's next hunt draws from a fresher pool of
  companies hiring right now. Inside a hunt this only trickles in (capped at 25 to
  fit the request budget); the cron runs it uncapped. It reuses the exact
  persistence path (`add_known_company`, idempotent on `(ats, slug)`), needs no
  new schema, and refuses to run against anything but the real Postgres DB. The
  `_SOURCES` list is the extension point for more discovery sources.

The workflow (`.github/workflows/daily-discovery.yml`) needs one secret,
`DATABASE_URL` — the same Postgres URL Vercel uses. This is the same pattern as
the existing `keep-warm.yml` cron that pings `/api/health` to dodge cold starts.

## Safety boundaries

- **No invented emails.** Resolve/verify surfaces unresolved leads rather than guessing an address to persist or send.
- **SSRF guard** (`netguard.py`): outbound scraping and SMTP probes refuse private/internal addresses.
- **User scoping**: every row belongs to a user; queries are always scoped.
- **Secrets**: Gmail credentials are Fernet-encrypted at rest, never returned to the client.
