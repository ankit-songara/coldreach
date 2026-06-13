"""
SQLAlchemy database engine and session management.
Uses SQLite by default; swap to PostgreSQL via DATABASE_URL env var.
"""

import logging
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from app.config import settings

log = logging.getLogger(__name__)


# ── Engine ───────────────────────────────────────────────────────────────────
connect_args = (
    {"check_same_thread": False}        # SQLite only
    if settings.database_url.startswith("sqlite")
    else {}
)

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    echo=settings.debug,
)

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
        ("user_id",         "INTEGER DEFAULT 1"),
        ("context",         "TEXT"),
    ],
    "users":            [("token_version", "INTEGER DEFAULT 0")],
    "email_drafts":     [("user_id", "INTEGER DEFAULT 1")],
    "resumes":          [("user_id", "INTEGER DEFAULT 1")],
    "scheduled_emails": [("user_id", "INTEGER DEFAULT 1")],
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
