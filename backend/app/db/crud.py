"""
Repository pattern: all database access goes through these classes.
Routes never touch SQLAlchemy directly.

Every data repository is scoped to a single user_id — passed in at construction
so callers physically cannot read or write another user's rows. The scheduler,
which runs outside a request, uses the admin helpers at the bottom.
"""

import re
from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.models import Contact, EmailDraft, Resume, ScheduledEmail, AppConfig, User, KnownCompany
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

    def count(self) -> int:
        return self.db.query(User).count()

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

    def create(self, data: ContactCreate) -> Contact:
        existing = self.get_by_email(data.email)
        if existing:
            return existing
        contact = Contact(user_id=self.user_id, **data.model_dump())
        self.db.add(contact)
        self.db.commit()
        self.db.refresh(contact)
        return contact

    def bulk_create(self, contacts: list[ContactCreate]) -> list[Contact]:
        """Insert new contacts, skip duplicates (per-user).

        Commits one row at a time so a unique-constraint violation from a
        concurrent hunt only skips that row instead of failing the whole batch.
        """
        created: list[Contact] = []
        for c in contacts:
            if self.get_by_email(c.email):
                continue
            obj = Contact(user_id=self.user_id, **c.model_dump())
            self.db.add(obj)
            try:
                self.db.commit()
            except IntegrityError:
                self.db.rollback()   # another request inserted it first
                continue
            self.db.refresh(obj)
            created.append(obj)
        return created

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
        self.db.delete(contact)
        self.db.commit()
        return True

    def delete_all(self) -> int:
        count = self._scoped().count()
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

    def delete_for_contact(self, contact_id: int) -> None:
        self._scoped().filter(EmailDraft.contact_id == contact_id).delete()
        self.db.commit()


# ── ScheduledEmail Repository ─────────────────────────────────────────────────
class ScheduledEmailRepository:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id

    def _scoped(self):
        return self.db.query(ScheduledEmail).filter(ScheduledEmail.user_id == self.user_id)

    def create(self, contact_id: int, subject: str, body: str,
               send_at: datetime, is_followup: bool = False) -> ScheduledEmail:
        item = ScheduledEmail(
            user_id=self.user_id, contact_id=contact_id, subject=subject, body=body,
            send_at=send_at, is_followup=is_followup, status="pending",
        )
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)
        return item

    def pending_for_contact(self, contact_id: int) -> list[ScheduledEmail]:
        return (
            self._scoped()
            .filter(ScheduledEmail.contact_id == contact_id,
                    ScheduledEmail.status == "pending")
            .all()
        )

    def all_pending(self) -> list[ScheduledEmail]:
        return (
            self._scoped()
            .filter(ScheduledEmail.status == "pending")
            .order_by(ScheduledEmail.send_at.asc())
            .all()
        )

    def cancel_followups_for_contact(self, contact_id: int) -> int:
        """Cancel all pending follow-ups for a contact (e.g. on reply). Returns count."""
        rows = (
            self._scoped()
            .filter(ScheduledEmail.contact_id == contact_id,
                    ScheduledEmail.status == "pending",
                    ScheduledEmail.is_followup == True)  # noqa: E712
            .all()
        )
        for r in rows:
            r.status = "cancelled"
        self.db.commit()
        return len(rows)

    def cancel(self, item_id: int) -> bool:
        item = self._scoped().filter(ScheduledEmail.id == item_id).first()
        if not item or item.status != "pending":
            return False
        item.status = "cancelled"
        self.db.commit()
        return True


# ── AppConfig Repository ──────────────────────────────────────────────────────
# Per-user keys: gmail_address, gmail_app_password (encrypted),
#                automation_enabled, daily_send_cap
class ConfigRepository:
    SECRET_KEYS = {"gmail_app_password"}

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

    def set(self, key: str, value: str) -> None:
        stored = security.encrypt(value) if key in self.SECRET_KEYS and value else value
        row = self._row(key)
        if row:
            row.value = stored
        else:
            self.db.add(AppConfig(user_id=self.user_id, key=key, value=stored))
        self.db.commit()

    def get_gmail_creds(self) -> tuple[str, str]:
        """Returns (address, app_password) — empty strings if not configured."""
        return self.get("gmail_address"), self.get("gmail_app_password")

    def automation_enabled(self) -> bool:
        return self.get("automation_enabled", "false") == "true"


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
        resume = Resume(user_id=self.user_id, text=text, filename=filename)
        self.db.add(resume)
        self.db.commit()
        self.db.refresh(resume)
        return resume


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


# ── Admin helpers for the background scheduler (cross-user, no request scope) ──
def all_due_scheduled(db: Session, now: datetime) -> list[ScheduledEmail]:
    return (
        db.query(ScheduledEmail)
        .filter(ScheduledEmail.status == "pending", ScheduledEmail.send_at <= now)
        .order_by(ScheduledEmail.send_at.asc())
        .all()
    )


def mark_scheduled_sent(db: Session, item_id: int) -> None:
    item = db.query(ScheduledEmail).filter(ScheduledEmail.id == item_id).first()
    if item:
        item.status = "sent"
        item.sent_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()


def mark_scheduled_failed(db: Session, item_id: int, error: str) -> None:
    item = db.query(ScheduledEmail).filter(ScheduledEmail.id == item_id).first()
    if item:
        item.status = "failed"
        item.error = error[:500]
        db.commit()
