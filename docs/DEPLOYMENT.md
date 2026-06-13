# Deployment Guide

ColdReach can be deployed on any platform that runs Docker. Below are instructions for the three most common options.

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
