# ColdReach

> Open-source cold outreach engine. Find hiring contacts, generate personalised emails. No vendor lock-in.

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue?logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green?logo=fastapi)](https://fastapi.tiangolo.com)
[![React 18](https://img.shields.io/badge/React-18-61dafb?logo=react)](https://react.dev)
[![License MIT](https://img.shields.io/badge/license-MIT-purple)](LICENSE)

---

## What it does

1. **Hunt** — scrapes HackerNews "Who is Hiring", GitHub commit emails, and company contact pages (Hunter.io enrichment optional)
2. **Verify** — syntax + MX + heuristic checks flag invalid/risky addresses before you send
3. **Compose** — generates designation-aware cold emails using any LLM (Ollama, Groq, OpenAI, OpenRouter, Anthropic)
4. **Send** — bulk-sends via Gmail SMTP with jitter and a daily cap, or schedules sends for later
5. **Automate** — queues follow-ups, auto-syncs Gmail (IMAP) to detect replies/bounces and cancels follow-ups accordingly
6. **Track** — CRM-style status per contact (new → emailed → followed_up → replied → interview → rejected/bounced)

Multi-user with email/password accounts; Gmail App Passwords are encrypted at rest.

---

## Tech Stack

| Layer      | Technology                        | Notes                                |
|------------|-----------------------------------|--------------------------------------|
| Backend    | Python 3.12 + FastAPI             | Async, auto OpenAPI docs             |
| Scraping   | Playwright + httpx                | Same engine as Apify internally      |
| LLM        | LangChain (any provider)          | Swap via one env var                 |
| Database   | SQLite → PostgreSQL               | Zero-setup default, swap for prod    |
| Frontend   | Vite + React 18 + TypeScript      | TailwindCSS, Zustand, TanStack Query |
| Local LLM  | Ollama                            | Free, private, runs on M-chip        |
| Cloud LLM  | Groq (free tier)                  | Fastest inference, no GPU needed     |

---

## Quick Start

### Option A — Docker (recommended)

```bash
git clone https://github.com/yourname/coldreach
cd coldreach

cp .env.example .env          # edit if needed — all defaults work locally

docker compose up -d

# First time: pull a local LLM (~5 GB, one-time)
docker exec coldreach-ollama ollama pull llama3.1
```

Open **http://localhost:5173** — done.

### Option B — Local dev (no Docker)

**Prerequisites:** Python 3.12+, Node 20+, [Ollama](https://ollama.com) or a Groq API key

```bash
# 1. Clone
git clone https://github.com/yourname/coldreach && cd coldreach

# 2. Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp ../.env.example ../.env

# 3. Frontend
cd ../frontend
npm install

# 4. Start (two terminals)
make dev-backend    # → http://localhost:8000
make dev-frontend   # → http://localhost:5173
```

---

## LLM Configuration

ColdReach auto-detects the best available LLM at startup:

```
LLM_PROVIDER=auto (default)
  → 1st: checks Ollama at localhost:11434
  → 2nd: uses Groq if LLM_API_KEY (Groq key) is set
  → otherwise: raises a clear error at compose time telling you what to configure
```

> There is also a zero-config `mock` provider for demos/CI. It is **only** used
> when you set `LLM_PROVIDER=mock` explicitly — it returns an obvious placeholder
> that must not be sent. `auto` never silently falls back to it.

### Ollama (local, free, private)

```bash
brew install ollama            # macOS
ollama pull llama3.1           # ~5GB, runs on M1/M2/M3 Macs

# Verify
curl http://localhost:11434/api/tags
```

Works without internet once the model is downloaded.

### Groq (cloud, free tier, fastest)

```
# console.groq.com → free API key → 14,400 tokens/min
GROQ_API_KEY=gsk_xxx
```

### Force a specific provider

```bash
# .env
LLM_PROVIDER=groq
LLM_API_KEY=gsk_xxx

# or OpenAI
LLM_PROVIDER=openai
LLM_API_KEY=sk-xxx
LLM_MODEL=gpt-4o-mini

# or OpenRouter (100+ models)
LLM_PROVIDER=openrouter
LLM_API_KEY=sk-or-xxx
LLM_MODEL=mistralai/mistral-7b-instruct
```

No code changes required — just environment variables.

---

## Project Structure

```
coldreach/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app + lifespan
│   │   ├── config.py            # Pydantic settings
│   │   ├── api/
│   │   │   ├── hunt.py          # POST /api/hunt
│   │   │   ├── compose.py       # POST /api/compose, /followup
│   │   │   └── contacts.py      # CRUD /api/contacts
│   │   ├── scrapers/            # Strategy pattern — add sources here
│   │   │   ├── base.py          # BaseScraper ABC
│   │   │   ├── hn.py            # HackerNews Algolia (free)
│   │   │   ├── github.py        # GitHub commit emails (free)
│   │   │   ├── web.py           # Playwright scraper
│   │   │   └── enricher.py      # Hunter.io (optional)
│   │   ├── llm/
│   │   │   ├── factory.py       # Provider factory + auto-detect
│   │   │   ├── generator.py     # Email generation
│   │   │   └── prompts.py       # Templates per designation type
│   │   └── db/
│   │       ├── database.py      # SQLAlchemy engine + session
│   │       ├── models.py        # ORM models
│   │       └── crud.py          # Repository pattern
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── api/                 # Typed API clients
│       ├── components/          # Setup, Hunt, Compose, Send tabs
│       ├── store/               # Zustand state
│       └── types/               # Shared TypeScript types
├── docker-compose.yml
├── Makefile
└── .env.example
```

---

## API Reference

Interactive docs at **http://localhost:8000/docs** (Swagger UI).

All endpoints except `/api/health`, `/api/auth/register`, and `/api/auth/login`
require a `Authorization: Bearer <token>` header.

| Method | Endpoint                  | Description                              |
|--------|---------------------------|------------------------------------------|
| GET    | /api/health               | Status + active LLM provider             |
| POST   | /api/auth/register        | Create an account, returns a token       |
| POST   | /api/auth/login           | Exchange credentials for a token         |
| POST   | /api/auth/logout          | Revoke all of this user's tokens         |
| GET    | /api/auth/me              | Current user                             |
| POST   | /api/hunt                 | Run all scrapers for a keyword           |
| POST   | /api/verify               | Verify contact emails (syntax/MX/heur.)  |
| GET    | /api/contacts             | List all saved contacts                  |
| POST   | /api/contacts             | Create contact manually                  |
| PATCH  | /api/contacts/{id}        | Update status / notes                    |
| DELETE | /api/contacts/{id}        | Delete one contact                       |
| POST   | /api/resume/extract       | Extract + save text from a PDF/DOCX      |
| GET    | /api/resume/latest        | Most recently saved résumé               |
| POST   | /api/compose              | Generate cold email via LLM              |
| POST   | /api/compose/followup     | Generate follow-up email                 |
| PUT    | /api/compose/draft/{id}   | Edit a draft's subject/body              |
| GET    | /api/compose/{id}         | List drafts for a contact                |
| POST   | /api/send/bulk            | Bulk-send drafts via Gmail SMTP          |
| POST   | /api/send/schedule        | Queue first-touch sends for later        |
| POST   | /api/send/test            | Verify Gmail credentials (no send)       |
| POST   | /api/inbox/sync           | Scan Gmail for replies/bounces (IMAP)    |
| POST   | /api/config/gmail         | Save Gmail creds server-side (encrypted) |
| GET    | /api/config               | Automation status (no secrets)           |
| POST   | /api/config/automation    | Toggle automation / set daily cap        |
| POST   | /api/followups/schedule   | Queue follow-ups N days out              |
| GET    | /api/followups            | List pending scheduled follow-ups        |
| DELETE | /api/followups/{id}       | Cancel a pending follow-up               |

---

## Adding a New Scraper

```python
# backend/app/scrapers/twitter.py
from app.scrapers.base import BaseScraper

class TwitterScraper(BaseScraper):
    name = "Twitter"

    async def search(self, query: str, **_) -> list[dict]:
        # your implementation
        return [{"name": ..., "email": ..., "company": ..., "designation": ..., "source": "Twitter"}]
```

Register it in `backend/app/api/hunt.py`:

```python
from app.scrapers.twitter import TwitterScraper

def _build_scrapers(hunter_key):
    return [
        HackerNewsScraper(),
        GitHubScraper(),
        WebScraper(),
        TwitterScraper(),    # ← add here
    ]
```

That's it.

---

## Production Deployment

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for:
- Railway / Render / Fly.io one-click deploy
- PostgreSQL swap
- Environment hardening

---

## Contributing

See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).

---

## License

MIT
