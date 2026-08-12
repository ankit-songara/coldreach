"""
SQLAlchemy database engine and session management.
Uses SQLite by default; swap to PostgreSQL via DATABASE_URL env var.
"""

import os
import logging
from pathlib import Path
from sqlalchemy import create_engine, inspect, text, event
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from app.config import settings

log = logging.getLogger(__name__)


# ── Engine ───────────────────────────────────────────────────────────────────
_is_sqlite = settings.database_url.startswith("sqlite")

# Ensure the SQLite file's parent directory exists (e.g. ./data) — otherwise the
# first connection fails with "unable to open database file" on a fresh checkout.
if _is_sqlite and ":memory:" not in settings.database_url:
    _prefix = "sqlite:///"
    if settings.database_url.startswith(_prefix):
        _db_path = settings.database_url[len(_prefix):]
        if _db_path:
            Path(_db_path).parent.mkdir(parents=True, exist_ok=True)

connect_args = (
    {"check_same_thread": False}        # SQLite only
    if _is_sqlite
    else {}
)

_engine_kwargs: dict = {"connect_args": connect_args, "echo": settings.debug}
if not _is_sqlite:
    # Recycle dead connections transparently (Supabase pooler drops idle ones).
    _engine_kwargs["pool_pre_ping"] = True
    if os.environ.get("VERCEL"):
        # Serverless: each invocation may freeze/thaw with the process. Holding a
        # client-side pool leaks connections against the pgbouncer pooler — open
        # per-use and close immediately instead.
        _engine_kwargs["poolclass"] = NullPool

engine = create_engine(settings.database_url, **_engine_kwargs)


# ── SQLite concurrency hardening ──────────────────────────────────────────────
# Concurrent request handlers can write at the same time. With the default
# rollback journal, SQLite serialises writers aggressively and throws
# "database is locked" under contention. WAL lets readers and one writer proceed
# concurrently; busy_timeout makes a blocked writer wait instead of failing fast.
if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")   # wait up to 5s for a lock
        cur.execute("PRAGMA synchronous=NORMAL")  # safe with WAL, much faster
        cur.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ── Base class for all ORM models ─────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


# ── Dependency for FastAPI routes ─────────────────────────────────────────────
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables() -> None:
    """Create all tables, then run a lightweight column migration."""
    from app.db import models   # noqa: F401 — import registers models
    Base.metadata.create_all(bind=engine)
    _ensure_columns()
    _fix_contact_email_index()
    _ensure_google_sub_index()
    _ensure_perf_indexes()


# Composite indexes matching the hot query paths. create_all() only builds
# indexes when it creates a table, so pre-existing tables need these added by
# hand. IF NOT EXISTS makes this an idempotent no-op on every later startup.
# Works on both SQLite and Postgres.
_PERF_INDEXES = (
    # GET /contacts — list scoped to user, ordered by created_at desc
    "CREATE INDEX IF NOT EXISTS ix_contacts_user_created ON contacts (user_id, created_at)",
    # status filters (compose/send tab splits)
    "CREATE INDEX IF NOT EXISTS ix_contacts_user_status ON contacts (user_id, status)",
    # daily send-cap count: last_emailed_at >= now-24h per user
    "CREATE INDEX IF NOT EXISTS ix_contacts_user_last_emailed ON contacts (user_id, last_emailed_at)",
    # drafts hydration + per-contact draft lookups
    "CREATE INDEX IF NOT EXISTS ix_drafts_user_contact ON email_drafts (user_id, contact_id)",
    # GET /compose/drafts/all — all of a user's drafts ordered newest-first
    "CREATE INDEX IF NOT EXISTS ix_drafts_user_created ON email_drafts (user_id, created_at)",
)


def _ensure_perf_indexes() -> None:
    with engine.begin() as conn:
        for ddl in _PERF_INDEXES:
            conn.execute(text(ddl))


# ── Minimal SQLite migration ──────────────────────────────────────────────────
# create_all() never ALTERs an existing table, so new columns added to a model
# won't appear on a pre-existing DB. We add any missing columns by hand.
_NEW_COLUMNS = {
    "contacts": [
        ("last_emailed_at", "DATETIME"),
        ("replied_at",      "DATETIME"),
        ("bounced",         "BOOLEAN DEFAULT 0"),
        ("followups_sent",  "INTEGER DEFAULT 0"),
        ("email_status",    "VARCHAR(20) DEFAULT 'unknown'"),
        ("confidence",      "INTEGER DEFAULT 0"),
        ("user_id",         "INTEGER DEFAULT 1"),
        ("context",         "TEXT"),
        ("linkedin_url",    "VARCHAR(255)"),
    ],
    "users":            [("token_version", "INTEGER DEFAULT 0"),
                         ("google_sub",    "VARCHAR(255)")],
    "email_drafts":     [("user_id", "INTEGER DEFAULT 1")],
    "resumes":          [("user_id", "INTEGER DEFAULT 1")],
    "app_config":       [("user_id", "INTEGER DEFAULT 1")],
}


def _ensure_columns() -> None:
    if not settings.database_url.startswith("sqlite"):
        return  # Postgres users should run a real migration tool
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, cols in _NEW_COLUMNS.items():
            if table not in existing_tables:
                continue
            have = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in cols:
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                    log.info(f"Migrated: added {table}.{name}")


def _ensure_google_sub_index() -> None:
    """
    On a fresh DB create_all() builds the unique index for users.google_sub; on a
    pre-existing DB the column was just added by _ensure_columns() without it. Add
    it by hand. SQLite treats NULLs as distinct, so password-only accounts (NULL
    google_sub) don't collide — only two accounts sharing a real sub would.
    """
    if not settings.database_url.startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return
    if "google_sub" not in {c["name"] for c in inspector.get_columns("users")}:
        return
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_google_sub "
            "ON users (google_sub)"
        ))


def _fix_contact_email_index() -> None:
    """
    Older schemas created a GLOBAL unique index on contacts.email. Email is now
    unique only per-user (UniqueConstraint user_id+email), so a leftover global
    unique index wrongly blocks a second user from saving an email another user
    already has. Replace any unique email index with a plain one, and ensure the
    per-user uniqueness index exists.
    """
    if not settings.database_url.startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "contacts" not in inspector.get_table_names():
        return
    with engine.begin() as conn:
        for idx in inspector.get_indexes("contacts"):
            if idx.get("unique") and idx.get("column_names") == ["email"]:
                conn.execute(text(f'DROP INDEX IF EXISTS "{idx["name"]}"'))
                conn.execute(text('CREATE INDEX IF NOT EXISTS ix_contacts_email ON contacts (email)'))
                log.info(f"Migrated: dropped global-unique index {idx['name']} on contacts.email")
        # Per-user uniqueness (no-op if it already exists).
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_contact_user_email "
            "ON contacts (user_id, email)"
        ))
