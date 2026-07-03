# ColdReach — Setup Guide

ColdReach is an open-source cold outreach engine with a FastAPI backend, React/Vite frontend, and local or cloud LLM support.

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.11+ | [python.org](https://python.org) or `brew install python@3.12` |
| Node.js | 18+ | [nodejs.org](https://nodejs.org) or `brew install node` |
| npm | any | bundled with Node |
| Git | any | [git-scm.com](https://git-scm.com) |

Docker is optional — only needed for the all-in-one Docker path.

---

## Option A — One-command setup (Mac/Linux)

```bash
git clone https://github.com/ankit-songara/coldreach
cd coldreach
bash run.sh
```

The script checks prerequisites, installs all dependencies, detects your LLM, runs tests, and opens the app at `http://localhost:5173`.

---

## Option B — Manual setup (Mac/Linux/Windows)

### 1. Clone the repo

```bash
git clone https://github.com/ankit-songara/coldreach
cd coldreach
```

### 2. Create your `.env` file

```bash
cp .env.example .env
```

Then open `.env` and configure at minimum one LLM option (see [LLM Configuration](#llm-configuration) below).

### 3. Install backend

```bash
cd backend
python3 -m venv .venv

# Mac/Linux
source .venv/bin/activate

# Windows
.venv\Scripts\activate

pip install -r requirements.txt
python -m playwright install chromium
```

### 4. Install frontend

```bash
cd ../frontend
npm install
```

### 5. Start backend (terminal 1)

```bash
cd backend
source .venv/bin/activate      # or .venv\Scripts\activate on Windows
uvicorn app.main:app --reload --port 8000
```

### 6. Start frontend (terminal 2)

```bash
cd frontend
npm run dev
```

Open `http://localhost:5173`. API docs at `http://localhost:8000/docs`.

---

## Option C — Docker Compose (all-in-one)

Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/).

```bash
git clone https://github.com/ankit-songara/coldreach
cd coldreach
cp .env.example .env        # edit as needed
docker compose up -d

# One-time: pull the local LLM model (~5 GB)
docker exec coldreach-ollama ollama pull llama3.1
```

Open `http://localhost:5173`.

```bash
docker compose logs -f      # tail logs
docker compose down         # stop everything
```

---

## LLM Configuration

Edit `.env` — pick one option:

### Option 1 — Groq (free cloud tier, recommended for first run)

1. Get a free API key at [console.groq.com](https://console.groq.com)
2. In `.env`:
   ```env
   LLM_PROVIDER=groq
   LLM_API_KEY=gsk_your_key_here
   ```

### Option 2 — Ollama (local, free, private)

```bash
# Mac
brew install ollama
ollama pull llama3.1        # ~5 GB, one-time

# Windows/Linux — download from https://ollama.com
ollama pull llama3.1
```

In `.env`:
```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
```

### Option 3 — OpenAI

```env
LLM_PROVIDER=openai
LLM_API_KEY=sk-your_key_here
```

### Option 4 — OpenRouter (100+ models)

```env
LLM_PROVIDER=openrouter
LLM_API_KEY=sk-or-your_key_here
LLM_MODEL=mistralai/mistral-7b-instruct
```

### No LLM configured

With `LLM_PROVIDER=auto` and no Ollama running and no key set, email generation
raises a clear error telling you what to configure — it does **not** silently
send unpersonalised text. Configure one of the options above before composing.
(For demos/CI only, set `LLM_PROVIDER=mock` to get an obvious, unsendable placeholder.)

---

## Optional Enrichment APIs

Add these to `.env` to improve contact discovery:

```env
# Hunter.io — email search by domain (25 free lookups/month)
HUNTER_API_KEY=your_key_here

# GitHub token — raises rate limit from 60 to 5,000 req/hr
GITHUB_TOKEN=your_token_here
```

---

## Makefile shortcuts

```bash
make setup          # install everything (run once after cloning)
make dev-backend    # start FastAPI with hot reload
make dev-frontend   # start Vite dev server
make test           # run backend test suite
make up             # start full stack via Docker Compose
make down           # stop Docker containers
make logs           # tail Docker container logs
make clean          # remove build artefacts
```

---

## Verify it's working

```bash
curl http://localhost:8000/api/health
```

Expected response includes `"status": "ok"` and the active LLM provider.

---

## Troubleshooting

**Python version error** — ensure `python3 --version` reports 3.11 or higher. On Windows use `python` instead of `python3`.

**Playwright install fails** — run with elevated permissions or add `--with-deps`:
```bash
python -m playwright install chromium --with-deps
```

**Port already in use** — another process is on port 8000 or 5173. Kill it or change the port in `uvicorn` / `vite.config.ts`.

**Ollama not detected** — make sure `ollama serve` is running before starting the backend. Check with `curl http://localhost:11434/api/tags`.

**CORS errors in browser** — confirm `CORS_ORIGINS=http://localhost:5173` is set in `.env`.

**Windows `.venv` activation** — if `Set-ExecutionPolicy` blocks the script, run:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

---

## Project structure

```
coldreach/
├── backend/          # FastAPI app
│   ├── app/
│   │   ├── api/      # route handlers
│   │   ├── db/       # SQLAlchemy models
│   │   ├── llm/      # LLM provider abstraction
│   │   ├── scrapers/ # web + enrichment scrapers
│   │   └── config.py # env-based settings
│   ├── tests/
│   └── requirements.txt
├── frontend/         # React + Vite + Tailwind
│   └── src/
├── docs/
│   ├── DEPLOYMENT.md # cloud deployment (Railway, Render, Fly.io)
│   └── CONTRIBUTING.md
├── docker-compose.yml
├── .env.example
├── Makefile
└── run.sh            # one-command setup + start
```

For cloud deployment see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
