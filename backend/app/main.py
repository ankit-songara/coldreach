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

    # One-time cleanup: hunts before the grounding fix persisted blind
    # careers@ guesses labeled as real role inboxes ("risky" status), which
    # bulk-send happily emailed — causing real bounces. Remove the ones that
    # were never emailed; anything already actioned keeps its history.
    # Idempotent: post-fix leads are either "valid" (grounded) or labeled
    # "(unverified guess)", so this matcher can never touch them.
    from app.db.migrations import purge_unverified_role_inbox_guesses
    _db = SessionLocal()
    try:
        purged = purge_unverified_role_inbox_guesses(_db)
        if purged:
            log.info(f"✓ Cleanup: removed {purged} pre-fix guessed role-inbox contacts")
    except Exception as e:
        log.warning(f"Guessed-contact cleanup skipped: {e}")
    finally:
        _db.close()

    try:
        provider, model = await detect_provider()
        log.info(f"✓ LLM ready: {provider}/{model}")
    except RuntimeError as e:
        log.warning(str(e))      # non-fatal — compose routes will error if called

    yield
    # ── Shutdown ──────────────────────────────────────────────────────────────
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
#
# Two layers: allow_origins is an exact-match list for known fixed origins
# (local dev, a custom domain if one gets added); allow_origin_regex catches
# every URL Vercel generates for the frontend project — its stable aliases
# AND the unique per-deployment hash URL that changes on every deploy, which
# no static list can keep up with. An origin allowed by EITHER layer passes.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=settings.cors_origin_regex or None,
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
    # llm_ok is all the frontend needs. The provider/model label is stack
    # detail — it's only exposed with DEBUG on, so a public deployment doesn't
    # advertise its internals to anyone who curls /api/health.
    try:
        provider, model = await detect_provider()
        llm_ok, llm_status = True, f"{provider}/{model}"
    except Exception as e:
        llm_ok, llm_status = False, f"unavailable: {e}"
    body = {"status": "ok", "version": settings.app_version, "llm_ok": llm_ok}
    if settings.debug:
        body["llm"] = llm_status
    return body
