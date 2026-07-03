<div align="center">

# ColdReach

### Cold-email your way into interviews.

**Job applications vanish into ATS black holes. The people who actually decide — founders, eng leads, recruiters — are one good email away.** ColdReach finds them, writes emails worth replying to, sends from your own Gmail, and tracks every lead from *sent → reply → interview → offer*.

Open-source · self-hosted · your Gmail, your LLM, your data — nothing leaves your machine.

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue?logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi)](https://fastapi.tiangolo.com)
[![React 18](https://img.shields.io/badge/React-18-61dafb?logo=react)](https://react.dev)
[![License MIT](https://img.shields.io/badge/license-MIT-purple)](LICENSE)

</div>

---

## The whole job search, as one funnel

ColdReach turns "apply and pray" into a measurable pipeline. Every contact moves through stages, and the dashboard shows where you convert and where you leak:

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
 GitHub       ██████████████████  27%   8/30 sent     ← your best source
 HackerNews   ███████████         19%   5/26 sent
 ATS boards   ████                 9%   3/32 sent
 Job boards   ██                   5%   1/19 sent
```

> **Who it's for:** early-career engineers reaching out to startups (esp. remote/US roles). That's where cold email actually lands — your sources are tuned for it.

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
   ┌─────────────┐   ┌──────────────┐   ┌──────────────┐   ┌────────────┐   ┌──────────────┐
   │  1. HUNT    │──▶│  2. RESOLVE  │──▶│  3. COMPOSE  │──▶│  4. SEND   │──▶│  5. TRACK    │
   │  10 sources │   │  + VERIFY    │   │   your LLM   │   │ your Gmail │   │  IMAP replies │
   └─────────────┘   └──────────────┘   └──────────────┘   └────────────┘   └──────────────┘
   who's hiring      real email +        designation-aware   paced, capped,   auto-detect
   right now         confidence score    personalised email  reputation-safe  replies/bounces
```

1. **Hunt** — scrapes 17 live sources (below) for people at companies hiring *right now*.
2. **Resolve & verify** — turns a name + company into a real address via pattern-learning + SMTP probing, scores confidence 0–100, and flags invalid/risky emails *before* you send (syntax + MX + Hunter.io if configured).
3. **Compose** — generates a designation-aware cold email (founder vs. eng-lead vs. recruiter) grounded in your résumé and genuine context captured at hunt time — never fabricated facts.
4. **Send** — bulk-sends through your own Gmail SMTP with human-like jitter, a daily cap, and a duplicate-send guard; or schedules sends for later.
5. **Track & automate** — syncs your Gmail over IMAP to detect replies and bounces, auto-cancels follow-ups when someone replies, and queues timed nudges for everyone who didn't.

You record outcomes (replied → interview → offer) in one tap, and the dashboard turns it into the funnel and source analytics above.

---

## Where it finds people (17 sources, ~170 company boards)

| Source | What it pulls | Cost |
|--------|---------------|------|
| **HackerNews** "Who is Hiring" | The current monthly thread — real posts with contact emails | Free |
| **HackerNews job posts** | "Acme (YC W24) Is Hiring" front-page stories — funded startups | Free |
| **GitHub** | Commit-author emails + profiles from orgs actively shipping in your stack | Free |
| **Greenhouse / Lever / Ashby** | Live job postings → company + recruiter leads | Free |
| **SmartRecruiters / Recruitee / Workable / Breezy** | More ATS boards (live-verified company slugs) | Free |
| **RemoteOK / Remotive / Arbeitnow** | Remote-job aggregators — proof a company is hiring | Free |
| **Jobicy / Himalayas / The Muse / WeWorkRemotely** | More remote-job boards | Free |
| **Hunter.io** *(optional)* | Verified emails + deliverability scores by domain | Free tier |

Searching is **role-aware** (`"golang hiring"`, `"react engineer remote"`) or **company-aware** (`"Stripe"`). Every discovered address runs through the verifier before it reaches you.

---

## Quick start

### Option A — Docker (recommended)

```bash
git clone https://github.com/ankit-songara/coldreach && cd coldreach
cp .env.example .env                 # all defaults work locally
docker compose up -d
docker exec coldreach-ollama ollama pull llama3.1   # one-time local LLM (~5 GB)
```

Open **http://localhost:5173** → done.

### Option B — Local dev

**Prereqs:** Python 3.11+, Node 18+, and either [Ollama](https://ollama.com) or a free [Groq](https://console.groq.com) key.

```bash
git clone https://github.com/ankit-songara/coldreach && cd coldreach
cp .env.example .env

# backend
cd backend
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium

# frontend
cd ../frontend && npm install

# run (two terminals, from repo root)
make dev-backend     # → http://localhost:8000
make dev-frontend    # → http://localhost:5173
```

> First run: create an account, paste/upload your résumé, add your Gmail **App Password** (Setup tab), then Hunt. Full walkthrough in [SETUP.md](SETUP.md).

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
LLM_PROVIDER=groq        LLM_API_KEY=gsk_...
LLM_PROVIDER=openai      LLM_API_KEY=sk-...      LLM_MODEL=gpt-4o-mini
LLM_PROVIDER=openrouter  LLM_API_KEY=sk-or-...   LLM_MODEL=mistralai/mistral-7b-instruct
LLM_PROVIDER=anthropic   LLM_API_KEY=sk-ant-...
```

> A zero-config `mock` provider exists for demos/CI — used **only** when you set `LLM_PROVIDER=mock`. It returns an obvious placeholder that must not be sent; `auto` never silently falls back to it.

---

## Privacy & safety (it's self-hosted for a reason)

- **Your Gmail, your account.** Sends go through *your* Gmail via an App Password — no third-party sending service ever sees your contacts.
- **Credentials encrypted at rest.** App Passwords are Fernet-encrypted before touching the database and are **never** persisted to the browser.
- **Multi-user, fully scoped.** Email/password accounts with revocable sessions; every row is scoped to its owner.
- **Reputation-safe sending.** Jittered pacing, a configurable daily cap, invalid-address skipping, and a guard that can't send a first-touch twice.
- **SSRF-guarded.** Server-side scraping and SMTP probing refuse to connect to private/internal addresses.
- **Nothing phones home.** No telemetry. Your résumé, contacts, and emails stay on your machine.

---

## Tech stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| Backend | Python 3.12 + FastAPI | Async, auto OpenAPI docs |
| Frontend | Vite + React 18 + TypeScript | Tailwind, Zustand, TanStack Query |
| LLM | LangChain (any provider) | Swap via one env var |
| Database | SQLite → PostgreSQL | Zero-setup default, swap for prod |
| Email | Gmail SMTP (send) + IMAP (reply detection) | Your account, App Password |
| Scraping | httpx + public APIs (+ Playwright) | Public endpoints only — no ToS-risky scraping |

---

## Extending it

**Add a generic source** — implement one method and register it:

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
    HackerNewsScraper(), HNJobsScraper(), GitHubScraper(),
    GreenhouseScraper(), LeverScraper(), AshbyScraper(),
    SmartRecruitersScraper(), RecruiteeScraper(),
    WorkableScraper(), BreezyScraper(),
    RemoteOKScraper(), RemotiveScraper(), ArbeitnowScraper(),
    MySourceScraper(),   # ← here
]
```

**Add an ATS** — subclass `BaseATSScraper` and implement `_fetch(slug)`, then add company rows to `scrapers/directory.py`.
**Add a job board** — subclass `_JsonBoard` in `scrapers/jobboards.py` and implement `_listings(client)`.

---

## API reference

Interactive Swagger UI at **http://localhost:8000/docs**. All routes except `/api/health`, `/api/auth/register`, and `/api/auth/login` require `Authorization: Bearer <token>`.

<details>
<summary><b>Endpoints</b></summary>

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /api/health | Status + active LLM provider |
| POST | /api/auth/register · /login · /logout | Accounts + revocable sessions |
| GET | /api/auth/me | Current user |
| POST | /api/hunt | Run all scrapers for a query |
| POST | /api/verify | Verify emails (syntax / MX / Hunter) |
| GET·POST·PATCH·DELETE | /api/contacts | Contact CRUD + status |
| POST | /api/resume/extract · /save | Extract/save résumé text (PDF/DOCX) |
| GET | /api/resume/latest | Most recent résumé |
| POST | /api/compose · /followup | Generate email / follow-up |
| PUT | /api/compose/draft/{id} | Edit a draft |
| POST | /api/send/bulk · /schedule · /test | Send now / queue / test creds |
| POST | /api/inbox/sync | Scan Gmail for replies & bounces |
| POST·GET | /api/config · /config/gmail · /config/automation | Server-side automation config |
| POST·GET·DELETE | /api/followups · /schedule · /{id} | Queue / list / cancel follow-ups |

</details>

---

## Project structure

```
backend/app/
├── main.py            # FastAPI app + lifespan + /api/health
├── config.py          # env settings        timeutil.py  netguard.py (SSRF)
├── security.py        # Fernet encryption + PBKDF2 + session tokens
├── mailer.py          # Gmail SMTP send (single + reusable session)
├── scheduler.py       # background follow-up delivery (paced)
├── verifier.py        # syntax + MX + Hunter deliverability checks
├── api/               # hunt · compose · contacts · resume · send · inbox · automation · verify · auth
├── scrapers/
│   ├── base.py        # BaseScraper ABC          directory.py  (company→ATS map)
│   ├── hn.py github.py                            # free people sources
│   ├── ats.py         # Greenhouse/Lever/Ashby/SmartRecruiters/Recruitee
│   ├── jobboards.py   # RemoteOK/Remotive/Arbeitnow
│   ├── resolver.py    # email pattern-learning + SMTP probe + confidence
│   ├── web.py         # company-page email harvest    enricher.py (Hunter)
├── llm/               # factory (auto-detect) · generator · prompts · parsing
└── db/                # database · models · crud (repository pattern)

frontend/src/          # api clients · components (Today/Setup/Hunt/Compose/Send) · store · types
```

---

## Roadmap

- [ ] **One-click hosted "lite"** + Gmail OAuth (remove the App-Password / self-host friction)
- [ ] **Chrome extension** — find the hiring manager straight from a LinkedIn/job post
- [ ] **A/B email variants** — test subjects/openers, learn what converts
- [ ] **Cross-user benchmarks** — "emails like yours reply at X%; top performers do Y"

---

## Docs

- [SETUP.md](SETUP.md) — full local / Docker setup + troubleshooting
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how the pieces fit together
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — Railway / Render / Fly.io + Postgres
- [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) — contributing guide

---

## License

[MIT](LICENSE) — use it, fork it, ship it.
