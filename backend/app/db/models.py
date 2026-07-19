"""SQLAlchemy ORM models for ColdReach."""

from datetime import datetime
from sqlalchemy import String, Text, DateTime, Boolean, LargeBinary, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db.database import Base


class User(Base):
    __tablename__ = "users"

    id:            Mapped[int]      = mapped_column(primary_key=True)
    email:         Mapped[str]      = mapped_column(String(255), unique=True, index=True)
    # Empty string for Google-only accounts (they never set a password). An empty
    # hash can never authenticate via /auth/login — verify_password rejects it.
    password_hash: Mapped[str]      = mapped_column(String(255))
    # Google account subject ("sub") claim — stable per-user id from Google.
    # NULL for password-only accounts; unique when set. Enables account linking
    # by matching a verified Google email to an existing password account.
    google_sub:    Mapped[str|None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    # Bumped on logout / password change to invalidate previously-issued tokens.
    token_version: Mapped[int]      = mapped_column(default=0)
    created_at:    Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<User {self.email}>"


class Contact(Base):
    __tablename__ = "contacts"
    # Email is unique per user (not globally) — prevents duplicate rows from
    # concurrent hunts racing the app-level get_by_email check.
    __table_args__ = (UniqueConstraint("user_id", "email", name="uq_contact_user_email"),)

    id:          Mapped[int]      = mapped_column(primary_key=True)
    user_id:     Mapped[int]      = mapped_column(index=True)
    name:        Mapped[str]      = mapped_column(String(255))
    # Unique per-user (enforced in the repository), not globally
    email:       Mapped[str]      = mapped_column(String(255), index=True)
    designation: Mapped[str]      = mapped_column(String(255), default="Hiring Manager")
    company:     Mapped[str]      = mapped_column(String(255), default="Unknown")
    source:      Mapped[str]      = mapped_column(String(255), default="")
    # Genuine, non-fabricated provenance context captured at hunt time — e.g. the
    # job posting this lead came from. Fed to the LLM so it can anchor on real
    # signal instead of inventing details.
    context:     Mapped[str|None] = mapped_column(Text,        nullable=True)
    status:      Mapped[str]      = mapped_column(String(50),  default="new")
    notes:       Mapped[str|None] = mapped_column(Text,        nullable=True)

    # ── Tracking (populated by send + inbox sync) ────────────────────────────
    last_emailed_at: Mapped[datetime|None] = mapped_column(DateTime, nullable=True, index=True)
    replied_at:      Mapped[datetime|None] = mapped_column(DateTime, nullable=True)
    bounced:         Mapped[bool]          = mapped_column(Boolean, default=False)
    followups_sent:  Mapped[int]           = mapped_column(default=0)
    # Email-verification verdict: unknown | valid | risky | invalid
    email_status:    Mapped[str]           = mapped_column(String(20), default="unknown")
    # Resolver confidence score: 0-100 (0 = unverified scrape, >50 = SMTP confirmed)
    confidence:      Mapped[int]           = mapped_column(default=0)

    created_at:  Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at:  Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<Contact {self.email}>"


class EmailDraft(Base):
    __tablename__ = "email_drafts"

    id:         Mapped[int]       = mapped_column(primary_key=True)
    user_id:    Mapped[int]       = mapped_column(index=True)
    contact_id: Mapped[int]       = mapped_column(index=True)
    subject:    Mapped[str]       = mapped_column(Text)
    body:       Mapped[str]       = mapped_column(Text)
    is_followup: Mapped[bool]     = mapped_column(default=False)
    created_at: Mapped[datetime]  = mapped_column(DateTime, server_default=func.now())


class ReplyMessage(Base):
    """
    Reply content captured by inbox sync — powers the v2 Replies inbox. Before
    this table, sync detected a reply, flipped the contact's status, and threw
    the message away. No FK constraints (matches the other tables); contact
    fields are joined at read time. Writes dedupe on
    (user_id, contact_id, received_at) so re-syncs never duplicate rows.
    """
    __tablename__ = "reply_messages"

    id:          Mapped[int]           = mapped_column(primary_key=True)
    user_id:     Mapped[int]           = mapped_column(index=True)
    contact_id:  Mapped[int]           = mapped_column(index=True)
    subject:     Mapped[str]           = mapped_column(String(500), default="")
    # First ~400 chars of the reply's plain-text body, whitespace-normalized.
    snippet:     Mapped[str]           = mapped_column(Text, default="")
    received_at: Mapped[datetime|None] = mapped_column(DateTime, nullable=True)
    created_at:  Mapped[datetime]      = mapped_column(DateTime, server_default=func.now())


class Resume(Base):
    __tablename__ = "resumes"

    id:         Mapped[int]      = mapped_column(primary_key=True)
    user_id:    Mapped[int]      = mapped_column(index=True)
    text:       Mapped[str]      = mapped_column(Text)
    filename:   Mapped[str|None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ResumeFile(Base):
    """
    The original uploaded résumé file (PDF/DOCX), one per user — attached to
    formal application emails (careers@ inboxes, recruiters). A separate table
    rather than columns on `resumes` so it needs no ALTER on pre-existing
    tables: create_all builds new tables on both SQLite and Postgres.
    """
    __tablename__ = "resume_files"

    user_id:    Mapped[int]      = mapped_column(primary_key=True)
    filename:   Mapped[str]      = mapped_column(String(255))
    mime:       Mapped[str]      = mapped_column(String(100))
    data:       Mapped[bytes]    = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AppConfig(Base):
    """
    Per-user key/value store for small configuration values — the signature
    name/links and the daily send cap. Primary key is (user_id, key).
    """
    __tablename__ = "app_config"

    user_id:    Mapped[int]      = mapped_column(primary_key=True)
    key:        Mapped[str]      = mapped_column(String(64), primary_key=True)
    value:      Mapped[str]      = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class EmailPattern(Base):
    """
    Learned email format per company domain ("acme.com uses first.last").

    Resolving a domain's pattern costs real work every hunt (GitHub commit
    search, SMTP probes) and the per-hunt ResolutionCache forgets it all when
    the hunt ends. Persisting the verdict makes every future hunt guess right
    on the first candidate — and bounces feed back as strikes so a pattern
    that stops working demotes itself instead of misleading forever.

    Global (not user-scoped), like KnownCompany: a company's email format is a
    fact about the company. A pattern is trusted while verified_count >
    bounced_count.
    """
    __tablename__ = "email_patterns"

    id:             Mapped[int]      = mapped_column(primary_key=True)
    domain:         Mapped[str]      = mapped_column(String(255), unique=True, index=True)
    pattern:        Mapped[str]      = mapped_column(String(32))   # e.g. "first.last"
    verified_count: Mapped[int]      = mapped_column(default=1)    # confirmations (SMTP/observed)
    bounced_count:  Mapped[int]      = mapped_column(default=0)    # strikes from bounce reports
    updated_at:     Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class KnownCompany(Base):
    """
    Runtime-extensible company → ATS directory entries (the CSV is the curated
    seed; these are added without code changes). Two origins:
      - source="user":       added by a user via /api/companies
      - source="discovered": auto-learned when a company-name hunt resolved to a
                             real ATS board

    Global (not user-scoped): a verified company→ATS mapping is a fact that
    benefits every hunt. Unique per (ats, slug).
    """
    __tablename__ = "known_companies"
    __table_args__ = (UniqueConstraint("ats", "slug", name="uq_known_company_ats_slug"),)

    id:         Mapped[int]      = mapped_column(primary_key=True)
    name:       Mapped[str]      = mapped_column(String(255))
    slug:       Mapped[str]      = mapped_column(String(255))
    ats:        Mapped[str]      = mapped_column(String(50))
    domain:     Mapped[str]      = mapped_column(String(255), default="")
    source:     Mapped[str]      = mapped_column(String(20), default="user")  # user | discovered
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
