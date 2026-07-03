"""
ColdReach API — entry point.

Startup sequence:
  1. Create SQLite tables
  2. Auto-detect LLM (Ollama → Groq fallback)
  3. Register routers
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db.database import create_tables
from app.llm.factory import detect_provider
from app.scheduler import scheduler
from app.api import hunt, compose, contacts, resume, send, inbox, automation, verify, auth, demo, companies

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(levelname)s  %(name)s  %(message)s",
)
log = logging.getLogger("coldreach")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    log.info(f"Starting {settings.app_name} v{settings.app_version}")
    create_tables()
    log.info("✓ Database ready")

    # Load runtime-extensible company directory entries (user-added + discovered).
    from app.db.database import SessionLocal
    from app.db.crud import load_known_companies_into_directory
    _db = SessionLocal()
    try:
        loaded = load_known_companies_into_directory(_db)
        if loaded:
            log.info(f"✓ Directory: +{loaded} learned companies")
    except Exception as e:
        log.warning(f"Could not load learned companies: {e}")
    finally:
        _db.close()

    try:
        provider, model = await detect_provider()
        log.info(f"✓ LLM ready: {provider}/{model}")
    except RuntimeError as e:
        log.warning(str(e))      # non-fatal — compose routes will error if called

    scheduler.start()           # background follow-up delivery
    log.info("✓ Follow-up scheduler running")

    yield
    # ── Shutdown ──────────────────────────────────────────────────────────────
    await scheduler.stop()
    log.info("Shutting down")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Open-source cold email engine. No vendor lock-in.",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
# Wildcards for methods/headers are rejected by browsers when credentials are
# allowed, so enumerate them explicitly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router,     prefix="/api")
app.include_router(hunt.router,     prefix="/api")
app.include_router(compose.router,  prefix="/api")
app.include_router(contacts.router, prefix="/api")
app.include_router(resume.router,   prefix="/api")
app.include_router(send.router,       prefix="/api")
app.include_router(inbox.router,      prefix="/api")
app.include_router(automation.router, prefix="/api")
app.include_router(verify.router,     prefix="/api")
app.include_router(demo.router,       prefix="/api")
app.include_router(companies.router,  prefix="/api")


@app.get("/api/health")
async def health():
    try:
        provider, model = await detect_provider()
        llm_status = f"{provider}/{model}"
    except Exception as e:
        llm_status = f"unavailable: {e}"
    return {
        "status":   "ok",
        "version":  settings.app_version,
        "llm":      llm_status,
    }
