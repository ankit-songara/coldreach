<div align="center">

# ColdReach

### Cold-email your way into interviews.

**Job applications vanish into ATS black holes. The people who actually decide — founders, eng leads, recruiters — are one good email away.** ColdReach finds them, writes emails worth replying to, sends from your own Gmail, and tracks every lead from *sent → reply → interview → offer*.

Open-source · self-hosted · your Gmail, your LLM, your data.

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue?logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi)](https://fastapi.tiangolo.com)
[![React 18](https://img.shields.io/badge/React-18-61dafb?logo=react)](https://react.dev)
[![License MIT](https://img.shields.io/badge/license-MIT-purple)](LICENSE)

</div>

---

## The whole job search, as one funnel

ColdReach turns "apply and pray" into a measurable pipeline. Every contact moves through stages, and the dashboard shows where you convert and where you leak *(numbers below are illustrative)*:

```
 Hunted     ████████████████████  142
 Verified   ████████████████      118     deliverable addresses only
 Drafted    █████████████          96     personalised by your résumé
 Sent       ███████████            80     via your Gmail, paced + capped
 Replied    ███                    18     23% reply rate
 Interview  █                       6     33% of replies
 Offer      ▏                       2     🎉
```

It also tells you **what's working** — reply rate by source — so you stop blasting low-yield channels and double down where you actually get answers:

```
 HackerNews   ██████████████████  27%   8/30 sent     ← your best source
 YC Startups  ███████████         19%   5/26 sent
 ATS boards   ████                 9%   3/32 sent
 Job boards   ██                   5%   1/19 sent
```

> **Who it's for:** early-career engineers reaching out to startups (esp. remote/US roles). That's where cold email actually lands — the sources are tuned for it.

---

## Screenshots

<!--
  Add real screenshots to docs/screenshots/ and they'll render here:
    today.png    – the Today dashboard (funnel + what's-working)
    hunt.png     – Hunt results with confidence badges
    compose.png  – a generated, personalised draft
  Capture at ~1280px wide on the warm light theme.
-->

| Today — funnel & analytics | Hunt — find & verify contacts | Compose — personalised drafts |
|---|---|---|
| ![Today dashboard](docs/screenshots/today.png) | ![Hunt](docs/screenshots/hunt.png) | ![Compose](docs/screenshots/compose.png) |

_(Screenshots live in `docs/screenshots/` — drop your own PNGs there.)_

---

## How it works

```
  Résumé ─┐
          ▼
   ┌─────────────┐   ┌──────────────┐   ┌──────────────┐   ┌────────────┐   ┌───────────────┐
   │  1. HUNT    │──▶│  2. RESOLVE  │──▶│  3. COMPOSE  │──▶│  4. SEND   │──▶│  5. TRACK     │
   │ 20+ sources │   │  + VERIFY    │   │   your LLM   │   │ your Gmail │   │  IMAP replies │
   └─────────────┘   └──────────────┘   └──────────────┘   └────────────┘   └───────────────┘
   who's hiring      real email +        designation-aware   paced, capped,   auto-detect
   right now         confidence score    personalised email  reputation-safe  replies/bounces
```

1. **Hunt** — scrapes 20+ live sources (below) for people and companies hiring *right now*.
2. **Resolve & verify** — turns a name + company into a real address via pattern-learning + SMTP probing, scores confidence 0–100, and flags invalid/risky emails *before* you send (syntax + MX + Hunter.io if configured). It **never invents an address it can't stand behind** — unresolved leads are surfaced, not faked.
3. **Compose** — generates a designation-aware cold email (founder vs. eng-lead vs. recruiter) grounded in your résumé and genuine context captured at hunt time — never fabricated facts.
4. **Send** — bulk-sends through your own Gmail SMTP with human-like jitter, a daily cap, and a duplicate-send guard; or schedules sends for later.
5. **Track & automate** — syncs your Gmail over IMAP to detect replies and bounces, auto-cancels follow-ups when someone replies, and queues timed nudges for everyone who didn't.

You record outcomes (replied → interview → offer) in one tap, and the dashboard turns it into the funnel and source analytics above.

---

## Where it finds people (20+ sources)

All sources are **keyless and free** — public APIs and endpoints only, no ToS-risky scraping.

| Group | Sources | What it pulls |
|-------|---------|---------------|
| **ATS boards** | Greenhouse · Lever · Ashby · SmartRecruiters · Recruitee · Workable · Breezy · Teamtailor · Workday | Live job postings → company + hiring-team leads |
| **Remote job boards** | RemoteOK · Remotive · Arbeitnow · Jobicy · Himalayas · The Muse · WeWorkRemotely · WorkingNomads | Proof a company is hiring right now |
| **Keyword search** | Workable search · SmartRecruiters search | Role-aware search across ATS postings |
| **Community / curated** | HackerNews "Who is Hiring" · YC startup pool | Real posts with contact emails; funded startups |
| **Enrichment** *(optional)* | Hunter.io | Verified emails + deliverability scores by domain (free tier) |

Searching is **role-aware** (`"golang hiring"`, `"react engineer remote"`) or **company-aware** (`"Stripe"`). Every discovered address runs through the verifier before it reaches you.

---

## Quick start

### Option A — Groq (fastest to a first email, free)

Grab a free key at [console.groq.com](https://console.groq.com), then:

```bash
git clone https://github.com/ankit-songara/coldreach && cd coldreach
cp .env.example .env
# in .env:  LLM_PROVIDER=groq   LLM_API_KEY=gsk_...

# backend
cd backend && python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt && python -m playwright install chromium

# frontend (new terminal)
cd frontend && npm install

# run (from repo root, two terminals)
make dev-backend     # → http://localhost:8000
make dev-frontend    # → http://localhost:5173
```

### Option B — Docker + local Ollama (fully offline, no cloud LLM)

```bash
git clone https://github.com/ankit-songara/coldreach && cd coldreach
cp .env.example .env                                  # defaults work locally
docker compose up -d
docker exec coldreach-ollama ollama pull llama3.1     # one-time local LLM (~5 GB)
```

Open **http://localhost:5173** → done.

> First run: create an account, paste/upload your résumé, add your Gmail **App Password** (Setup tab), then Hunt. Full walkthrough + troubleshooting in [SETUP.md](SETUP.md).

---

## LLM configuration

ColdReach is provider-agnostic and auto-detects the best available LLM at startup:

```
LLM_PROVIDER=auto (default)
  → 1st: Ollama at localhost:11434   (local, free, private)
  → 2nd: Groq if LLM_API_KEY is set  (cloud, free tier, fastest)
  → else: a clear error at compose time telling you what to configure
```

Force any provider with env vars — no code changes:

```bash
LLM_PROVIDER=groq        LLM_API_KEY=gsk_...     # default model: openai/gpt-oss-20b
LLM_PROVIDER=openai      LLM_API_KEY=sk-...      LLM_MODEL=gpt-4o-mini
LLM_PROVIDER=openrouter  LLM_API_KEY=sk-or-...   LLM_MODEL=mistralai/mistral-7b-instruct
LLM_PROVIDER=anthropic   LLM_API_KEY=sk-ant-...
```

On Groq, the default is `openai/gpt-oss-20b` — fast enough (~1000 tok/s) to fit a serverless request wall and gentle on free-tier rate limits. If a Groq model is ever retired, the app automatically falls back through a candidate chain (see [`llm/factory.py`](backend/app/llm/factory.py)).

> A zero-config `mock` provider exists for demos/CI — used **only** when you set `LLM_PROVIDER=mock`. It returns an obvious placeholder that must not be sent; `auto` never silently falls back to it.

---

## Privacy & safety (it's self-hosted for a reason)

- **Your Gmail, your account.** Sends go through *your* Gmail via an App Password (or one-click OAuth) — no third-party sending service ever sees your contacts.
- **Credentials encrypted at rest.** App Passwords are Fernet-encrypted before touching the database and are **never** persisted to the browser.
- **Multi-user, fully scoped.** Email/password (and optional Google) accounts with revocable sessions; every row is scoped to its owner.
- **Reputation-safe sending.** Jittered pacing, a configurable daily cap, invalid-address skipping, and a guard that can't send a first-touch twice.
- **SSRF-guarded.** Server-side scraping and SMTP probing refuse to connect to private/internal addresses.
- **Nothing phones home.** No telemetry.

---

## Tech stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| Backend | Python 3.12 + FastAPI | Async, auto OpenAPI docs |
| Frontend | Vite + React 18 + TypeScript | Tailwind, Zustand, TanStack Query |
| LLM | LangChain (any provider) | Swap via one env var |
| Database | SQLite → PostgreSQL | Zero-setup default; Postgres for shared/prod |
| Email | Gmail SMTP (send) + IMAP (reply detection) | Your account, App Password or OAuth |
| Scraping | httpx + public APIs (+ Playwright) | Public endpoints only |

**Hosted MVP:** the maintained instance runs on Vercel serverless (backend + frontend as two projects) with a Supabase Postgres database and Groq for the LLM — a fully free/no-card stack. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

---

## Extending it

**Add an ATS** — subclass `BaseATSScraper` in [`scrapers/ats.py`](backend/app/scrapers/ats.py), implement `_fetch(slug)`, add company slugs to [`scrapers/directory.py`](backend/app/scrapers/directory.py).

**Add a job board** — subclass `_JsonBoard` in [`scrapers/jobboards.py`](backend/app/scrapers/jobboards.py) and implement `_listings(client)`.

**Add any other source** — subclass `BaseScraper`, then register it in `_build_scrapers()` in [`api/hunt.py`](backend/app/api/hunt.py):

```python
# backend/app/scrapers/mysource.py
from app.scrapers.base import BaseScraper

class MySourceScraper(BaseScraper):
    name = "MySource"
    async def search(self, query: str, **_) -> list[dict]:
        return [{"name": ..., "email": ..., "company": ..., "designation": ..., "source": "MySource"}]
```

```python
# backend/app/api/hunt.py — add to _build_scrapers()
scrapers = [
    GreenhouseScraper(), LeverScraper(), AshbyScraper(),
    # …existing sources…
    HackerNewsScraper(),
    YCStartupsScraper(),
    MySourceScraper(),   # ← here
]
```

---

## API reference

Interactive Swagger UI at **http://localhost:8000/docs**. Everything is mounted under `/api`. All routes except `/api/health`, `/api/auth/register`, and `/api/auth/login` require `Authorization: Bearer <token>`.

<details>
<summary><b>Endpoints</b></summary>

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /api/health | Status + active LLM provider |
| POST | /api/auth/register · /login · /google · /logout | Accounts (email/password + Google) + revocable sessions |
| GET | /api/auth/me | Current user |
| GET · POST | /api/hunt · /hunt/suggestions | Run all scrapers / dynamic query suggestions |
| GET·POST·PATCH·DELETE | /api/contacts · /contacts/{id} | Contact CRUD, status, LinkedIn attach |
| POST·GET | /api/resume/extract · /save · /latest | Extract/save/fetch résumé text (PDF/DOCX) |
| POST·PUT·GET | /api/compose · /followup · /draft/{id} · /drafts/all | Generate email / follow-up / edit / list drafts |
| POST | /api/send/bulk | Bulk send now (paced, capped, dedup-guarded) |
| POST·GET | /api/inbox/sync · /replies | Scan Gmail for replies & bounces / list replies |
| GET·POST·DELETE | /api/config · /config/gmail · /config/gmail/oauth/* | Profile + Gmail creds (App Password or OAuth) |
| GET·POST·DELETE | /api/companies | Saved target companies |
| GET | /api/analytics/summary | Funnel + source analytics |
| POST·DELETE | /api/demo/seed | Seed / clear demo data |

</details>

---

## Project structure

```
backend/app/
├── main.py            # FastAPI app + lifespan + /api/health + router mounts
├── config.py          # env settings (pydantic-settings)
├── deps.py            # FastAPI dependencies (auth, db session)
├── security.py        # Fernet encryption + PBKDF2 + session tokens
├── netguard.py        # SSRF guard for outbound scraping/SMTP
├── timeutil.py        # timezone-aware helpers
├── mailer.py          # Gmail SMTP send (single + reusable session)
├── verifier.py        # syntax + MX + Hunter deliverability checks
├── gmail_oauth.py     # one-click "Connect Gmail" OAuth flow
├── api/               # auth · hunt · compose · contacts · resume · send ·
│                      #   inbox · config · companies · analytics · demo
├── schemas/           # Pydantic request/response models (contact, email)
├── scrapers/
│   ├── base.py        # BaseScraper / BaseATSScraper ABCs
│   ├── ats.py         # Greenhouse/Lever/Ashby/SmartRecruiters/Recruitee/Workable/Breezy/Teamtailor
│   ├── workday.py     # Workday tenant scraper
│   ├── jobboards.py   # RemoteOK/Remotive/Arbeitnow/Jobicy/Himalayas/TheMuse/WWR/WorkingNomads + searches
│   ├── hackernews.py  # HN "Who is Hiring" thread
│   ├── yc.py          # YC startup pool (fills leftover funnel slots)
│   ├── directory.py   # company → ATS-slug map
│   ├── resolver.py    # email pattern-learning + SMTP probe + confidence
│   ├── web.py         # company-page email harvest
│   └── enricher.py    # Hunter.io (optional)
├── llm/               # factory (auto-detect) · generator · prompts · parsing · quality · relevance
└── db/                # database · models · crud (repository pattern) · migrations

frontend/src/          # api clients · components (Today/Hunt/Compose/Send/Setup/Replies/Analytics/Landing)
                       #   · hooks · store (Zustand) · lib · types
```

---

## Roadmap

- [x] Gmail OAuth one-click connect (alongside App Password)
- [x] "Sign in with Google"
- [x] Daily background discovery — a GitHub Actions cron harvests fresh "who's hiring" companies into the shared directory (no serverless timeout)
- [ ] Chrome extension — find the hiring manager straight from a LinkedIn/job post
- [ ] A/B email variants — test subjects/openers, learn what converts
- [ ] Cross-user benchmarks — "emails like yours reply at X%; top performers do Y"

---

## Docs

- [SETUP.md](SETUP.md) — full local / Docker setup + troubleshooting
- [AGENTS.md](AGENTS.md) — orientation for AI coding agents (architecture, conventions, invariants)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — domains, patterns, and how the pieces fit
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — Vercel + Supabase (hosted MVP) and Docker hosts
- [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) — contributing guide

---

## License

[MIT](LICENSE) — use it, fork it, ship it.
