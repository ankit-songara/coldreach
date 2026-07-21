"""
Repository pattern: all database access goes through these classes.
Routes never touch SQLAlchemy directly.

Every data repository is scoped to a single user_id — passed in at construction
so callers physically cannot read or write another user's rows.
"""

import re
from datetime import datetime, timedelta
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.models import (
    Contact, EmailDraft, Resume, ResumeFile, AppConfig, User, KnownCompany, EmailPattern,
    ReplyMessage, HuntCursor,
)
from app.schemas.contact import ContactCreate, ContactUpdate
from app.schemas.email import DraftCreate
from app import security


# Statuses that mean a contact has already received their first-touch email.
# (A manual "open in Gmail" send sets status="emailed" but not last_emailed_at,
#  so we check status too, not just the timestamp.)
ALREADY_CONTACTED_STATUSES = {"emailed", "followed_up", "replied", "interview", "offer", "rejected"}


def already_first_touched(contact: Contact) -> bool:
    """True if a first-touch email should NOT be sent again to this contact."""
    return contact.last_emailed_at is not None or contact.status in ALREADY_CONTACTED_STATUSES


# ── User Repository (not user-scoped — it manages the users themselves) ───────
class UserRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, user_id: int) -> User | None:
        return self.db.query(User).filter(User.id == user_id).first()

    def get_by_email(self, email: str) -> User | None:
        return self.db.query(User).filter(User.email == email.lower().strip()).first()

    def get_by_google_sub(self, google_sub: str) -> User | None:
        return self.db.query(User).filter(User.google_sub == google_sub).first()

    def create(self, email: str, password: str) -> User:
        user = User(email=email.lower().strip(), password_hash=security.hash_password(password))
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def create_google_user(self, email: str, google_sub: str) -> User:
        """Create a Google-only account. Empty password_hash → password login
        is impossible for this account (verify_password rejects an empty hash)."""
        user = User(email=email.lower().strip(), password_hash="", google_sub=google_sub)
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def link_google_sub(self, user: User, google_sub: str) -> None:
        """Attach a Google identity to an existing (password) account so the same
        person signing in either way lands on one account."""
        user.google_sub = google_sub
        self.db.commit()

    def bump_token_version(self, user_id: int) -> None:
        """Invalidate all existing sessions for a user (logout / password change)."""
        user = self.get_by_id(user_id)
        if user:
            user.token_version = (user.token_version or 0) + 1
            self.db.commit()


# ── Contact Repository ────────────────────────────────────────────────────────
class ContactRepository:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id

    def _scoped(self):
        return self.db.query(Contact).filter(Contact.user_id == self.user_id)

    def get_all(self) -> list[Contact]:
        return self._scoped().order_by(Contact.created_at.desc()).all()

    def count_emailed_since(self, since: datetime) -> int:
        """SQL-side count of contacts emailed after `since` — used for the daily
        send cap. Replaces fetching every row and counting in Python."""
        return (
            self._scoped()
            .filter(Contact.last_emailed_at.isnot(None), Contact.last_emailed_at >= since)
            .count()
        )

    def get_by_id(self, contact_id: int) -> Contact | None:
        return self._scoped().filter(Contact.id == contact_id).first()

    def get_by_email(self, email: str) -> Contact | None:
        return self._scoped().filter(Contact.email == email).first()

    def all_email_names(self) -> list[tuple[str, str]]:
        """Every (email, name) this user owns, emails lowercased — one
        two-column SELECT. Feeds the hunt's exclusion set (skip already-owned
        leads before spending resolve budget) and seeds the resolution cache
        (an owned real person is grounded pattern evidence for their domain)."""
        return [
            ((e or "").lower(), n or "")
            for (e, n) in self._scoped().with_entities(Contact.email, Contact.name).all()
            if e
        ]

    def create(self, data: ContactCreate) -> Contact:
        existing = self.get_by_email(data.email)
        if existing:
            return existing
        contact = Contact(user_id=self.user_id, **data.model_dump())
        self.db.add(contact)
        self.db.commit()
        self.db.refresh(contact)
        return contact

    def bulk_create(self, contacts: list[ContactCreate]) -> tuple[list[Contact], list[Contact]]:
        """Insert new contacts, skip duplicates (per-user). Returns
        (created, existing) — the pre-existing rows that made a lead a
        duplicate, so the hunt can SHOW the user which contacts those were
        instead of a bare count.

        Commits one row at a time so a unique-constraint violation from a
        concurrent hunt only skips that row instead of failing the whole batch.
        """
        created:  list[Contact] = []
        existing: list[Contact] = []
        for c in contacts:
            prior = self.get_by_email(c.email)
            if prior:
                existing.append(prior)
                continue
            obj = Contact(user_id=self.user_id, **c.model_dump())
            self.db.add(obj)
            try:
                self.db.commit()
            except IntegrityError:
                self.db.rollback()   # another request inserted it first
                prior = self.get_by_email(c.email)
                if prior:
                    existing.append(prior)
                continue
            self.db.refresh(obj)
            created.append(obj)
        return created, existing

    def update(self, contact_id: int, data: ContactUpdate) -> Contact | None:
        contact = self.get_by_id(contact_id)
        if not contact:
            return None
        for key, val in data.model_dump(exclude_unset=True).items():
            setattr(contact, key, val)
        self.db.commit()
        self.db.refresh(contact)
        return contact

    def delete(self, contact_id: int) -> bool:
        contact = self.get_by_id(contact_id)
        if not contact:
            return False
        # No FK/cascade on these tables (SQLite can't add one retroactively), so
        # remove the contact's drafts explicitly or they orphan forever.
        self.db.query(EmailDraft).filter(
            EmailDraft.user_id == self.user_id,
            EmailDraft.contact_id == contact_id,
        ).delete(synchronize_session=False)
        self.db.delete(contact)
        self.db.commit()
        return True

    def delete_all(self) -> int:
        count = self._scoped().count()
        self.db.query(EmailDraft).filter(
            EmailDraft.user_id == self.user_id
        ).delete(synchronize_session=False)
        self._scoped().delete()
        self.db.commit()
        return count


# ── EmailDraft Repository ─────────────────────────────────────────────────────
class DraftRepository:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id

    def _scoped(self):
        return self.db.query(EmailDraft).filter(EmailDraft.user_id == self.user_id)

    def get_for_contact(self, contact_id: int) -> list[EmailDraft]:
        return (
            self._scoped()
            .filter(EmailDraft.contact_id == contact_id)
            .order_by(EmailDraft.created_at.desc())
            .all()
        )

    def get_all(self) -> list[EmailDraft]:
        """Every draft for this user, newest first — lets the frontend hydrate
        all contacts' drafts in ONE request instead of one request per contact."""
        return self._scoped().order_by(EmailDraft.created_at.desc()).all()

    def get_by_id(self, draft_id: int) -> EmailDraft | None:
        return self._scoped().filter(EmailDraft.id == draft_id).first()

    def create(self, data: DraftCreate) -> EmailDraft:
        draft = EmailDraft(user_id=self.user_id, **data.model_dump())
        self.db.add(draft)
        self.db.commit()
        self.db.refresh(draft)
        return draft

    def update_content(self, draft_id: int, subject: str, body: str) -> EmailDraft | None:
        draft = self.get_by_id(draft_id)
        if not draft:
            return None
        draft.subject = subject
        draft.body = body
        self.db.commit()
        self.db.refresh(draft)
        return draft

# ── ReplyMessage Repository ───────────────────────────────────────────────────
class ReplyRepository:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id

    def _scoped(self):
        return self.db.query(ReplyMessage).filter(ReplyMessage.user_id == self.user_id)

    def add_if_new(self, contact_id: int, subject: str, snippet: str,
                   received_at: datetime | None) -> ReplyMessage | None:
        """Persist a captured reply — idempotent on (contact_id, received_at) so
        re-syncing the same inbox never duplicates rows. Returns None on skip."""
        existing = self._scoped().filter(
            ReplyMessage.contact_id == contact_id,
            ReplyMessage.received_at == received_at,
        ).first()
        if existing:
            return None
        row = ReplyMessage(
            user_id=self.user_id, contact_id=contact_id,
            subject=(subject or "")[:500], snippet=snippet or "",
            received_at=received_at,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def latest_with_contacts(self, limit: int = 100) -> list[tuple[ReplyMessage, Contact]]:
        """Newest-first (ReplyMessage, Contact) pairs for this user. Inner join —
        a reply whose contact was deleted disappears with it (no FK cascade
        exists, so orphaned rows are simply never shown)."""
        return (
            self.db.query(ReplyMessage, Contact)
            .filter(
                ReplyMessage.user_id == self.user_id,
                Contact.user_id == self.user_id,
                Contact.id == ReplyMessage.contact_id,
            )
            .order_by(ReplyMessage.received_at.desc(), ReplyMessage.id.desc())
            .limit(limit)
            .all()
        )


# ── AppConfig Repository ──────────────────────────────────────────────────────
# Per-user keys: sender_name, signature_links, daily_send_cap,
#                gmail_address, gmail_app_password (encrypted at rest),
#                gmail_oauth_address, gmail_oauth_refresh_token (encrypted)
class ConfigRepository:
    SECRET_KEYS = {"gmail_app_password", "gmail_oauth_refresh_token"}

    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id

    def _row(self, key: str):
        return (
            self.db.query(AppConfig)
            .filter(AppConfig.user_id == self.user_id, AppConfig.key == key)
            .first()
        )

    def get(self, key: str, default: str = "") -> str:
        row = self._row(key)
        if not row:
            return default
        if key in self.SECRET_KEYS and row.value:
            return security.decrypt(row.value)
        return row.value

    def get_gmail_creds(self) -> tuple[str, str]:
        """(address, app_password) — empty strings if not connected.
        Password decrypts via SECRET_KEY; stored value never leaves the server."""
        return self.get("gmail_address"), self.get("gmail_app_password")

    def get_gmail_oauth(self) -> tuple[str, str]:
        """(address, refresh_token) for the one-click OAuth connection —
        empty strings if not connected. Token decrypts via SECRET_KEY."""
        return self.get("gmail_oauth_address"), self.get("gmail_oauth_refresh_token")

    def set(self, key: str, value: str) -> None:
        stored = security.encrypt(value) if key in self.SECRET_KEYS and value else value
        row = self._row(key)
        if row:
            row.value = stored
        else:
            self.db.add(AppConfig(user_id=self.user_id, key=key, value=stored))
        try:
            self.db.commit()
        except IntegrityError:
            # Concurrent insert on the same (user_id, key) — retry as an update.
            self.db.rollback()
            row = self._row(key)
            if row:
                row.value = stored
                self.db.commit()


# ── Resume Repository ─────────────────────────────────────────────────────────
class ResumeRepository:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id

    def get_latest(self) -> Resume | None:
        return (
            self.db.query(Resume)
            .filter(Resume.user_id == self.user_id)
            .order_by(Resume.id.desc())
            .first()
        )

    def save(self, text: str, filename: str | None = None) -> Resume:
        """Upsert: overwrite the latest résumé instead of inserting a new row.

        Every 'Save Resume' click used to append a full-text row, growing the
        table without bound; only get_latest() was ever read back.
        """
        existing = self.get_latest()
        if existing:
            existing.text = text
            existing.filename = filename
            self.db.commit()
            self.db.refresh(existing)
            return existing
        resume = Resume(user_id=self.user_id, text=text, filename=filename)
        self.db.add(resume)
        self.db.commit()
        self.db.refresh(resume)
        return resume

    def save_file(self, filename: str, mime: str, data: bytes) -> ResumeFile:
        """Upsert the original uploaded file — one per user, latest upload wins."""
        existing = self.get_file()
        if existing:
            existing.filename, existing.mime, existing.data = filename, mime, data
            self.db.commit()
            self.db.refresh(existing)
            return existing
        rf = ResumeFile(user_id=self.user_id, filename=filename, mime=mime, data=data)
        self.db.add(rf)
        self.db.commit()
        self.db.refresh(rf)
        return rf

    def get_file(self) -> ResumeFile | None:
        return self.db.query(ResumeFile).filter(ResumeFile.user_id == self.user_id).first()

    def has_file(self) -> bool:
        """Existence check without loading the (potentially large) blob."""
        return (
            self.db.query(ResumeFile.user_id)
            .filter(ResumeFile.user_id == self.user_id)
            .first()
        ) is not None


# ── Sender-name resolution (for email greetings/signatures) ───────────────────
_NAME_WORD = re.compile(r"^[A-Za-z][A-Za-z.\-']*$")


_URL_RE   = re.compile(r'https?://|www\.|linkedin\.com|github\.com', re.IGNORECASE)
_PHONE_RE = re.compile(r'[\+\(]?\d[\d\s\-\(\)\.]{6,}')


def _name_from_resume(text: str) -> str:
    """Best-effort: scan the first few non-empty lines for a plausible name.

    Skips lines that look like URLs, phone numbers, email addresses, or
    location strings (contain digits or known URL patterns). Gives up after
    the first 5 non-empty lines so we don't wander into the body.
    """
    checked = 0
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        checked += 1
        if checked > 5:
            break
        # Skip obvious non-name lines
        if "@" in s:
            continue
        if any(ch.isdigit() for ch in s):
            continue
        if _URL_RE.search(s):
            continue
        if _PHONE_RE.search(s):
            continue
        # Candidate: 2–4 words, all name-like tokens
        words = s.split()
        if 2 <= len(words) <= 4 and all(_NAME_WORD.match(w) for w in words):
            return " ".join(w.capitalize() for w in words)
    return ""


def resolve_sender_name(db: Session, user_id: int, user_email: str = "") -> str:
    """
    Resolve the name to sign emails with, in priority order:
      1. an explicit `sender_name` saved in config
      2. the name on the first line of the user's latest résumé
      3. a name derived from their email local-part (last resort)
    """
    cfg = ConfigRepository(db, user_id)
    explicit = cfg.get("sender_name", "").strip()
    if explicit:
        return explicit

    latest = ResumeRepository(db, user_id).get_latest()
    if latest:
        from_resume = _name_from_resume(latest.text)
        if from_resume:
            return from_resume

    local = (user_email or "").split("@")[0]
    local = re.sub(r"\d+", "", local)              # strip digits (e.g. ...2003)
    parts = [p for p in re.split(r"[._\-]+", local) if p]
    return " ".join(p.capitalize() for p in parts)


# ── Signature-link extraction from the résumé ─────────────────────────────────
# High-precision patterns only: a wrong link in a signature is worse than none.
# GitHub must be a bare profile (no /repo path) — "github.com/acme/widgets" in a
# work-history bullet is an employer's repo, not the candidate.
_LINKEDIN_RE = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[A-Za-z0-9_%\-\.]+", re.IGNORECASE)
_GITHUB_RE   = re.compile(r"(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9\-]+(?!/)", re.IGNORECASE)
# Personal-site guess: a bare domain on a personal-links TLD.
_SITE_RE     = re.compile(
    r"(?:https?://)?(?:www\.)?[A-Za-z0-9\-]+\.(?:dev|me|io|tech|xyz|site|codes|page)\b(?:/[^\s,;)]*)?"
)


def _clean_url(u: str) -> str:
    u = u.strip().rstrip(".,;:)")
    return re.sub(r"^https?://(www\.)?", "", u)


def extract_links_from_resume(text: str) -> str:
    """Best-effort LinkedIn / GitHub / personal-site links from résumé text,
    formatted as the one signature line ('a · b · c'). Empty string if none.

    Only the header and footer are searched — that's where candidates put their
    own links; URLs in the body are usually employers, products, or projects.
    """
    lines = (text or "").splitlines()
    regions = "\n".join(lines[:10] + lines[-5:])
    links: list[str] = []
    m = _LINKEDIN_RE.search(regions)
    if m:
        links.append(_clean_url(m.group(0)))
    m = _GITHUB_RE.search(regions)
    if m:
        links.append(_clean_url(m.group(0)))
    m = _SITE_RE.search(regions)
    if m:
        site = _clean_url(m.group(0))
        if not any(site.split("/")[0] in l for l in links):
            links.append(site)
    return " · ".join(links[:3])


def resolve_signature_links(db: Session, user_id: int) -> str:
    """
    Resolve the signature link line, in priority order:
      1. explicit `signature_links` saved in config
      2. links auto-extracted from the latest résumé
    """
    cfg = ConfigRepository(db, user_id)
    explicit = cfg.get("signature_links", "").strip()
    if explicit:
        return explicit
    latest = ResumeRepository(db, user_id).get_latest()
    if latest:
        return extract_links_from_resume(latest.text)
    return ""


# ── Known companies (runtime-extensible ATS directory; global, not user-scoped) ─
def list_known_companies(db: Session) -> list[KnownCompany]:
    return db.query(KnownCompany).order_by(KnownCompany.created_at.desc()).all()


def add_known_company(db: Session, name: str, slug: str, ats: str,
                      domain: str = "", source: str = "user") -> KnownCompany | None:
    """Persist a company→ATS mapping and register it in the live directory.

    Idempotent on (ats, slug). Returns the row (existing or new), or None on
    invalid input.
    """
    from app.scrapers import directory
    name, slug, ats = name.strip(), slug.strip(), (ats or "").strip().lower()
    if not (name and slug and ats):
        return None
    existing = db.query(KnownCompany).filter(
        KnownCompany.ats == ats, KnownCompany.slug == slug
    ).first()
    if existing:
        return existing
    kc = KnownCompany(name=name, slug=slug, ats=ats, domain=(domain or "").strip().lower(), source=source)
    db.add(kc)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()   # a concurrent hunt inserted the same (ats, slug)
        return db.query(KnownCompany).filter(
            KnownCompany.ats == ats, KnownCompany.slug == slug
        ).first()
    db.refresh(kc)
    directory.register(name, slug, ats, kc.domain)
    return kc


def delete_known_company(db: Session, company_id: int) -> bool:
    from app.scrapers import directory
    kc = db.query(KnownCompany).filter(KnownCompany.id == company_id).first()
    if not kc:
        return False
    directory.unregister(kc.ats, kc.slug)
    db.delete(kc)
    db.commit()
    return True


def load_known_companies_into_directory(db: Session) -> int:
    """Register all persisted companies into the in-memory directory (startup)."""
    from app.scrapers import directory
    n = 0
    for kc in db.query(KnownCompany).all():
        if directory.register(kc.name, kc.slug, kc.ats, kc.domain):
            n += 1
    return n


# ── Email pattern memory (global, like KnownCompany) ──────────────────────────

# ── Hunt exploration cursor ───────────────────────────────────────────────────
# Which ATS boards a user's repeat hunts already probed for a query, so each
# re-run covers a fresh directory slice. Same cross-request-memory precedent as
# EmailPattern/KnownCompany, but user-scoped: exploration is per person.

_CURSOR_TTL = timedelta(days=7)     # postings churn — stale coverage must retry
_CURSOR_MAX_SLUGS = 400             # belt-and-braces bound on the JSON payload


def get_explored_slugs(db: Session, user_id: int, query_norm: str) -> set[str]:
    """'ats:slug' keys already probed for this (user, query). Empty when the
    cursor is absent or older than the TTL (lazy expiry — no cron)."""
    row = db.get(HuntCursor, (user_id, query_norm))
    if row is None or row.updated_at is None:
        return set()
    if row.updated_at < datetime.utcnow() - _CURSOR_TTL:
        return set()
    return set((row.explored or {}).get("ats_slugs", []))


def record_explored_slugs(db: Session, user_id: int, query_norm: str, new_keys: set[str]) -> None:
    """Merge this hunt's completed probes into the cursor (upsert). A stale
    cursor is overwritten, not merged — its coverage already expired."""
    if not new_keys:
        return
    row = db.get(HuntCursor, (user_id, query_norm))
    stale = row is not None and row.updated_at is not None         and row.updated_at < datetime.utcnow() - _CURSOR_TTL
    prior: set[str] = set() if (row is None or stale) else         set((row.explored or {}).get("ats_slugs", []))
    merged = sorted(prior | new_keys)[:_CURSOR_MAX_SLUGS]
    if row is None:
        row = HuntCursor(user_id=user_id, query_norm=query_norm)
        db.add(row)
    row.explored = {"ats_slugs": merged}
    row.updated_at = datetime.utcnow()
    db.commit()


def get_domain_patterns(db: Session, domains: list[str]) -> dict[str, str]:
    """Trusted pattern per domain — only rows whose confirmations outweigh
    bounce strikes. One query for a whole hunt's worth of domains."""
    wanted = [d.lower().strip() for d in domains if d]
    if not wanted:
        return {}
    rows = db.query(EmailPattern).filter(EmailPattern.domain.in_(wanted)).all()
    return {r.domain: r.pattern for r in rows if r.verified_count > r.bounced_count}


def record_domain_pattern(db: Session, domain: str, pattern: str, verified: bool) -> None:
    """Upsert a learned pattern. Same pattern again → another confirmation.
    A DIFFERENT pattern replaces the old one only when it arrives SMTP-verified;
    an unverified observation never overwrites a verified record. Best-effort —
    a race or constraint error must never break a hunt."""
    domain, pattern = domain.lower().strip(), (pattern or "").strip()
    if not (domain and pattern):
        return
    try:
        row = db.query(EmailPattern).filter(EmailPattern.domain == domain).first()
        if row is None:
            db.add(EmailPattern(domain=domain, pattern=pattern,
                                verified_count=2 if verified else 1))
        elif row.pattern == pattern:
            row.verified_count += 2 if verified else 1
        elif verified:
            # Contradicting evidence, but ours is SMTP-confirmed — replace.
            row.pattern = pattern
            row.verified_count = 2
            row.bounced_count = 0
        db.commit()
    except IntegrityError:
        db.rollback()
    except Exception:
        db.rollback()


def record_pattern_bounce(db: Session, email: str) -> None:
    """A bounce at this domain is a strike against its stored pattern. Once
    strikes reach confirmations the pattern stops being trusted (and the next
    hunt re-learns it from scratch)."""
    domain = (email or "").rsplit("@", 1)[-1].lower().strip()
    if not domain:
        return
    try:
        row = db.query(EmailPattern).filter(EmailPattern.domain == domain).first()
        if row is not None:
            row.bounced_count += 1
            db.commit()
    except Exception:
        db.rollback()
