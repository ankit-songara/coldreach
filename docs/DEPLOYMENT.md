# Deployment Guide

ColdReach can be deployed on any platform that runs Docker, or serverless on Vercel. Below are instructions for the most common options.

---

## Option 0 — Vercel (serverless, two projects)

Deploy **backend** and **frontend** as two separate Vercel projects.

### Backend (`backend/` as project root)

`backend/vercel.json` already configures the Python function (entry `api/index.py`,
`maxDuration: 60`). Set these environment variables — the first two are **required**:

| Variable | Value | Why |
|---|---|---|
| `SECRET_KEY` | a Fernet key (see below) | Sessions/secrets break without it — the app refuses to boot on Vercel if missing |
| `DATABASE_URL` | `postgresql://…` (Neon/Supabase) | Vercel's filesystem is ephemeral — SQLite won't persist |
| `LLM_PROVIDER` | `groq` | Ollama can't run on serverless |
| `LLM_API_KEY` | `gsk_…` | Groq API key |
| `CORS_ORIGINS` | `https://your-frontend.vercel.app` | Exact frontend origin |
| `GOOGLE_CLIENT_ID` | (optional) | Enables "Sign in with Google" |
| `GITHUB_TOKEN` | (optional) | Better email-pattern detection |

Generate the key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

### Frontend (`frontend/` as project root)

| Variable | Value |
|---|---|
| `VITE_API_URL` | `https://your-backend.vercel.app/api` |
| `VITE_GOOGLE_CLIENT_ID` | (optional) same client ID as the backend |

### Serverless caveats

- **SMTP from Vercel is unreliable**: Gmail often rejects logins from datacenter
  IPs (`5.7.14`). The "open in Gmail" per-contact button always works; bulk SMTP
  sending may not. Use a VM/container host if bulk sending matters.
- SMTP email-verification probes are disabled automatically (port 25 is blocked);
  hunts fall back to pattern heuristics + MX checks.
- There is no background worker — sending happens only from the browser session.

---

## Option A — Railway (easiest, ~5 min)

Railway auto-detects Docker and deploys from GitHub.

```bash
# 1. Push to GitHub
git push origin main

# 2. railway.app → New Project → Deploy from GitHub → select repo

# 3. Add environment variables in Railway dashboard:
#    DATABASE_URL  postgresql://...  (Railway Postgres plugin)
#    LLM_PROVIDER  groq
#    LLM_API_KEY   gsk_...

# 4. Done — Railway gives you a URL
```

**Services to create:**
- `backend` — root `./backend`, port `8000`
- `frontend` — root `./frontend`, port `5173`
- `Postgres` — Railway plugin (free tier available)

**Note:** Ollama can't run on Railway free tier (needs persistent storage + memory). Use Groq in cloud deployments.

---

## Option B — Render

```bash
# render.yaml (place in repo root)
```

```yaml
services:
  - type: web
    name: coldreach-backend
    runtime: docker
    dockerfilePath: ./backend/Dockerfile
    envVars:
      - key: DATABASE_URL
        fromDatabase:
          name: coldreach-db
          property: connectionString
      - key: LLM_PROVIDER
        value: groq
      - key: LLM_API_KEY
        sync: false        # set manually in dashboard

  - type: web
    name: coldreach-frontend
    runtime: docker
    dockerfilePath: ./frontend/Dockerfile

databases:
  - name: coldreach-db
    plan: free
```

---

## Option C — Fly.io

```bash
# Backend
cd backend
fly launch --name coldreach-backend --dockerfile Dockerfile
fly secrets set LLM_PROVIDER=groq LLM_API_KEY=gsk_xxx

# Frontend
cd ../frontend
fly launch --name coldreach-frontend --dockerfile Dockerfile
```

---

## Switching to PostgreSQL

Change `DATABASE_URL` in `.env` (or dashboard env vars):

```env
DATABASE_URL=postgresql://user:password@host:5432/coldreach
```

Add `psycopg2-binary` to `backend/requirements.txt`:
```
psycopg2-binary>=2.9.9
```

No code changes needed — SQLAlchemy handles both.

---

## Production Checklist

- [ ] `DEBUG=false` in env
- [ ] `CORS_ORIGINS` set to your frontend domain only
- [ ] `DATABASE_URL` points to PostgreSQL (not SQLite)
- [ ] `LLM_PROVIDER` set explicitly (not `auto`)
- [ ] `SECRET_KEY` set if you add auth later
- [ ] `GITHUB_TOKEN` set for higher rate limits
- [ ] HTTPS enabled (Railway/Render/Fly handle this automatically)
