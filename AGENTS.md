# AGENTS.md

Orientation for AI coding agents (and humans) working in this repo. Read this before making changes.

## What ColdReach is

A keyless, free cold-outreach tool for job seekers: **hunt** hiring contacts from public sources → **resolve/verify** their email → **compose** a personalized email with an LLM → **send** from the user's own Gmail → **track** replies over IMAP. FastAPI backend (`backend/`), React + Vite + TS frontend (`frontend/src`).

It's an **MVP for ~10 users** — favor simple, correct, low-maintenance code over abstractions, scale work, or new infra. Don't add agents, queues, workers, or services unless asked.

## Runtime reality (this shapes almost every decision)

The maintained instance runs on a **free/no-credit-card** stack. Keep it that way.

- **Vercel serverless**, region `sin1`. Backend and frontend are two separate Vercel projects. **Hard 60s function wall** — anything slower than ~55s fails as a 502/timeout. Compose, hunt, and inbox-sync all live under this ceiling.
- **Supabase Postgres** via SQLAlchemy **NullPool** — every request opens a fresh TLS connection (no pooling on serverless). Minimize round trips; avoid N+1s; batch where you can. Locally the default is SQLite (`data/coldreach.db`).
- **Groq free tier** for the LLM. Rate-limited by **requests/min AND tokens/min**; models get retired without notice. This is the compose bottleneck, not the code.
- **Gmail SMTP is unreliable from datacenter IPs** — bulk send from Vercel may be rejected (`5.7.14`); the per-contact "open in Gmail" path always works. SMTP verify probes (port 25) are disabled on serverless; hunts fall back to MX + pattern heuristics.

## Commands

```bash
# Backend (from backend/, venv active)
pytest -q                         # full suite (~500 tests) — MUST stay green
uvicorn app.main:app --reload     # dev server on :8000

# Frontend (from frontend/)
npm run build                     # tsc + vite build — the type/lint gate
npm run dev                       # Vite dev on :5173

# From repo root
make dev-backend / make dev-frontend / make test
```

**Always** run `pytest -q` after backend changes and `npm run build` after frontend changes. Do not claim a change works without one of these passing.

## Invariants — do not break these

1. **Never persist or send an invented email address.** The resolver/verifier surfaces unresolved leads; it does not fabricate. Any change touching `scrapers/resolver.py`, `verifier.py`, or contact creation must preserve this.
2. **`LLM_PROVIDER=auto` never falls back to `mock`.** The mock is an obvious unsendable placeholder reachable only via explicit `LLM_PROVIDER=mock`. `auto` with no working provider raises a clear compose-time error instead.
3. **Keyless & free.** Scrapers use public endpoints only — no paid APIs, no scraping that needs auth. Optional enrichment (Hunter, GitHub token) degrades gracefully when absent.
4. **Every DB row is user-scoped.** Multi-user app; never return or mutate another user's data. Auth is Bearer-token (not cookie).
5. **Secrets stay server-side and encrypted.** Gmail App Passwords are Fernet-encrypted at rest and never sent to the browser.

## Architecture in one screen

- **Scrapers** (`scrapers/`): each implements `BaseScraper` (or `BaseATSScraper`); `api/hunt.py::_build_scrapers()` composes ~21 of them and runs them with `asyncio.gather`, then dedupes. See `docs/ARCHITECTURE.md`.
- **LLM** (`llm/`): `factory.py` picks the provider once and caches it; `generator.py` runs a two-pass quality loop with a time budget (skips the regen pass if the first draft ate the wall), and a **candidate-chain fallback** when a Groq model is decommissioned (`_GROQ_FALLBACKS`, `_RETIRED_MODELS`). Rate-limit (429) errors are **not** retried — retrying into a throttled window makes it worse; they propagate as an honest 429.
- **DB** (`db/`): repository pattern (`ContactRepository`, etc.) — routes never touch SQLAlchemy directly. `migrations.py` runs lightweight idempotent DDL on startup.
- **Frontend**: Zustand store + TanStack Query. Tabs are kept-alive (never remount) and `refetchOnWindowFocus:false`, so **any query without explicit invalidation will show stale data** — invalidate/patch the cache after mutations.

## Gotchas that have bitten us

- **`backend/vercel.json` routing.** The `fastapi` framework preset changed rewrite semantics; a catch-all `rewrites → /api/index` breaks all routing (caused a full 404 outage). The committed config uses `installCommand` + `regions:["sin1"]` + `functions.maxDuration:60` and **no catch-all rewrite** — don't reintroduce one.
- **Prod Supabase DDL needs explicit human approval in chat** before applying (schema changes are irreversible on shared data).
- **`llm/generator.py` is double-spaced** (a blank line between most lines — a formatting artifact). Match the surrounding style when editing it; don't reflow the whole file inside an unrelated change.
- **`useDismissAnimation`'s one-shot `closing` latch** is only safe for conditionally-mounted overlays, not always-mounted ones.
- **Don't burst-test compose** — a handful of rapid calls exhausts the Groq free-tier quota and makes every subsequent call fail, which looks like a code bug but isn't. Test one call, wait.

## Conventions

- **Comments explain *why*, not *what*.** This codebase's comments are mostly intentional — decisions, invariants, gotchas. Preserve them. When adding code, match the surrounding comment density and idiom.
- Keep diffs minimal and focused; don't opportunistically reformat unrelated code.
- Config is env-driven (`config.py` / pydantic-settings) — no hardcoded secrets, no code changes needed to switch providers or DBs.
- Prefer clear, boring solutions. This is a small app maintained part-time.
