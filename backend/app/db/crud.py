"""
Repository pattern: all database access goes through these classes.
Routes never touch SQLAlchemy directly.

Every data repository is scoped to a single user_id — passed in at construction
so callers physically cannot read or write another user's rows.
"""

import re
from datetime import datetime, timedelta
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, defer
from app.db.models import (
    Contact, EmailDraft, Resume, ResumeFile, AppConfig, User, KnownCompany, EmailPattern,
    ReplyMessage, HuntCursor, CompanyTag, ScrapeCache,
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
        # context (up to 4KB of scraped posting text per row) is deferred: no
        # get_all() consumer reads it — ContactOut doesn't serialize it, and
        # send/inbox/analytics/hunt only touch identity/status columns — yet
        # every list call was dragging ~KBs × N rows out of Supabase. Compose,
        # the one context reader, fetches per-contact via get_by_id (eager).
        # If a future caller DOES touch .context it still works (lazy loads,
        # one query per row) — just move that caller off get_all() then.
        return (
            self._scoped()
            .options(defer(Contact.context))
            .order_by(Contact.created_at.desc())
            .all()
        )

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

    def get_by_ids(self, contact_ids: list[int]) -> list[Contact]:
        """Several contacts in ONE query — bulk send resolves a whole chunk at
        once instead of a SELECT (and a Supabase round trip) per id."""
        if not contact_ids:
            return []
        return self._scoped().filter(Contact.id.in_(contact_ids)).all()

    def mark_emailed(self, contact_ids: list[int], when: datetime) -> None:
        """Bulk 'these just went out' update — one UPDATE + one commit for the
        whole batch instead of update()+commit()+refresh() per contact."""
        if not contact_ids:
            return
        (self._scoped()
             .filter(Contact.id.in_(contact_ids))
             .update({Contact.status: "emailed", Contact.last_emailed_at: when},
                     synchronize_session=False))
        self.db.commit()

    def claim_for_send(self, contact_ids: list[int], when: datetime) -> set[int]:
        """Atomically claim contacts for a first-touch send. The WHERE re-checks
        the not-yet-touched state INSIDE the UPDATE, so of two overlapping
        bulk-send requests only one can claim each row — the other request's
        claim matches nothing and it skips those contacts instead of
        double-sending the same cold email. Returns the ids actually claimed."""
        if not contact_ids:
            return set()
        res = self.db.execute(
            sa_update(Contact)
            .where(Contact.user_id == self.user_id,
                   Contact.id.in_(contact_ids),
                   Contact.last_emailed_at.is_(None),
                   Contact.status.notin_(ALREADY_CONTACTED_STATUSES))
            .values(status="emailed", last_emailed_at=when)
            .returning(Contact.id)
            .execution_options(synchronize_session=False))
        claimed = {row[0] for row in res}
        self.db.commit()
        return claimed

    def release_send_claim(self, contact_ids: list[int], claim_ts: datetime) -> None:
        """Give back claims whose send FAILED, restoring first-touch
        eligibility. Guarded on the claim timestamp so it can only undo THIS
        request's claim, never a genuine send recorded by someone else."""
        if not contact_ids:
            return
        (self._scoped()
             .filter(Contact.id.in_(contact_ids),
                     Contact.last_emailed_at == claim_ts)
             .update({Contact.status: "new", Contact.last_emailed_at: None},
                     synchronize_session=False))
        self.db.commit()

    def mark_addresses_invalid(self, contact_ids: list[int]) -> None:
        """Flag addresses Gmail rejected outright at send time (SMTPRecipients-
        Refused) as invalid, so the send eligibility filter skips them next time
        instead of re-attempting a dead address on every bulk send (each hard
        rejection is a small sender-reputation ding)."""
        if not contact_ids:
            return
        (self._scoped()
             .filter(Contact.id.in_(contact_ids))
             .update({Contact.email_status: "invalid"}, synchronize_session=False))
        self.db.commit()

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

        Batched on purpose: the old per-row SELECT+INSERT+COMMIT (plus a refresh
        each) made persistence O(N) round-trips to the DB — which grew with the
        user's contact count and, after the hunt's time budget, pushed broad
        hunts past the serverless wall. Now it's: ONE existence query, ONE
        batched insert/commit, and NO per-row reload. `expire_on_commit=False`
        keeps the inserted rows' attributes readable after commit (the caller
        serialises id/name/email/… but never created_at), so building the
        response costs zero extra queries. Falls back to per-row only on a
        concurrent-insert race, so a lost race still skips just the racing row.
        """
        # Dedupe input by email — one hunt can surface the same address from
        # several sources; a batch with two identical (user_id, email) rows would
        # violate the unique constraint and fail the whole insert.
        by_email: dict[str, ContactCreate] = {}
        for c in contacts:
            by_email.setdefault(c.email, c)
        if not by_email:
            return [], []

        emails = list(by_email.keys())
        # ONE query for the already-owned rows among these emails — this is also
        # exactly the "existing/duplicate" set the hunt reports.
        existing = (
            self.db.query(Contact)
            .filter(Contact.user_id == self.user_id, Contact.email.in_(emails))
            .all()
        )
        owned = {c.email for c in existing}
        to_insert = [c for em, c in by_email.items() if em not in owned]
        if not to_insert:
            return [], existing

        objs = [Contact(user_id=self.user_id, **c.model_dump()) for c in to_insert]
        prev_expire = self.db.expire_on_commit
        self.db.expire_on_commit = False
        try:
            self.db.add_all(objs)
            self.db.commit()          # one batched round-trip; PKs populated in place
            return objs, existing
        except IntegrityError:
            # A concurrent hunt inserted one of these — fall back to per-row so
            # only the racing row is skipped, not the whole batch.
            self.db.rollback()
            created: list[Contact] = []
            existing_by_email = {c.email: c for c in existing}
            for c in to_insert:
                prior = self.get_by_email(c.email)
                if prior:
                    existing_by_email.setdefault(c.email, prior)
                    continue
                obj = Contact(user_id=self.user_id, **c.model_dump())
                self.db.add(obj)
                try:
                    self.db.commit()
                    created.append(obj)
                except IntegrityError:
                    self.db.rollback()
                    p = self.get_by_email(c.email)
                    if p:
                        existing_by_email.setdefault(c.email, p)
            return created, list(existing_by_email.values())
        finally:
            self.db.expire_on_commit = prev_expire

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

    def first_touch_for_contacts(self, contact_ids: list[int]) -> dict[int, EmailDraft]:
        """Newest first-touch (non-followup) draft per contact, in ONE query.
        Bulk send used to run get_for_contact() per contact — a Supabase round
        trip each, before any mail moved. Newest-first ordering means the first
        row seen per contact is the one to keep."""
        if not contact_ids:
            return {}
        rows = (
            self._scoped()
            .filter(EmailDraft.contact_id.in_(contact_ids),
                    EmailDraft.is_followup.is_(False))
            .order_by(EmailDraft.created_at.desc())
            .all()
        )
        out: dict[int, EmailDraft] = {}
        for d in rows:
            out.setdefault(d.contact_id, d)
        return out

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

    def get_many(self, keys: list[str]) -> dict[str, str]:
        """Every requested value in ONE SELECT ("" for missing keys), secret
        keys decrypted exactly like get(). GET /config fired on every app load
        used to make ~6 serial single-key round trips to Supabase for these."""
        rows = (
            self.db.query(AppConfig)
            .filter(AppConfig.user_id == self.user_id, AppConfig.key.in_(keys))
            .all()
        )
        out = {k: "" for k in keys}
        for r in rows:
            v = r.value or ""
            if r.key in self.SECRET_KEYS and v:
                v = security.decrypt(v)
            out[r.key] = v
        return out

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


def resolve_sender_name(db: Session, user_id: int, user_email: str = "", *,
                        cfg_values: dict | None = None,
                        resume_text: str | None = None) -> str:
    """
    Resolve the name to sign emails with, in priority order:
      1. an explicit `sender_name` saved in config
      2. the name on the first line of the user's latest résumé
      3. a name derived from their email local-part (last resort)

    cfg_values / resume_text: optional preloaded data so a caller resolving
    BOTH signature fields (GET /config's _status) pays one config SELECT and
    one résumé read total, instead of re-querying per resolver.
    """
    if cfg_values is not None:
        explicit = (cfg_values.get("sender_name") or "").strip()
    else:
        explicit = ConfigRepository(db, user_id).get("sender_name", "").strip()
    if explicit:
        return explicit

    if resume_text is None:
        latest = ResumeRepository(db, user_id).get_latest()
        resume_text = latest.text if latest else ""
    if resume_text:
        from_resume = _name_from_resume(resume_text)
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


def resolve_signature_links(db: Session, user_id: int, *,
                            cfg_values: dict | None = None,
                            resume_text: str | None = None) -> str:
    """
    Resolve the signature link line, in priority order:
      1. explicit `signature_links` saved in config
      2. links auto-extracted from the latest résumé

    cfg_values / resume_text: optional preloads (see resolve_sender_name).
    """
    if cfg_values is not None:
        explicit = (cfg_values.get("signature_links") or "").strip()
    else:
        explicit = ConfigRepository(db, user_id).get("signature_links", "").strip()
    if explicit:
        return explicit
    if resume_text is None:
        latest = ResumeRepository(db, user_id).get_latest()
        resume_text = latest.text if latest else ""
    return extract_links_from_resume(resume_text) if resume_text else ""


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
# Bound on the JSON payload. Must comfortably exceed the directory size or the
# cursor saturates and stops recording coverage (~100KB of JSON at 3000 — fine).
_CURSOR_MAX_SLUGS = 3000


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
    prior: list[str] = [] if (row is None or stale) else         list((row.explored or {}).get("ats_slugs", []))
    # LRU order, evicting the OLDEST coverage first: keep prior order, move
    # re-probed keys to the recent end, append new keys, trim from the front.
    # (The old `sorted(...)[:cap]` evicted ALPHABETICALLY — once saturated,
    # alphabetically-late slugs could never be recorded and were re-probed
    # forever.)
    kept = [k for k in prior if k not in new_keys]
    merged = (kept + sorted(new_keys))[-_CURSOR_MAX_SLUGS:]
    if row is None:
        row = HuntCursor(user_id=user_id, query_norm=query_norm)
        db.add(row)
    row.explored = {"ats_slugs": merged}
    row.updated_at = datetime.utcnow()
    db.commit()


# ── Shared scrape cache ───────────────────────────────────────────────────────
# A popular query's network scrape (ATS/board/company-page fan-out) is identical
# for everyone and is the biggest slice of a hunt's wall-clock. Cache the raw
# output globally so the first hunter pays for it and everyone else within the
# TTL skips it. Short TTL because postings churn; lazy expiry (no cron on
# serverless). See ScrapeCache for the cross-user-safety reasoning.
_SCRAPE_CACHE_TTL = timedelta(hours=4)
# Guard against a pathological payload bloating the row / JSON round-trip; a real
# hunt caps far below this.
_SCRAPE_CACHE_MAX_LEADS = 2000


def get_scrape_cache(
    db: Session, query_norm: str, scrapers_sig: list[str]
) -> tuple[list, list] | None:
    """Cached ``(results_per_scraper, probed)`` for this query, or ``None`` on a
    miss / expiry / scraper-set mismatch (the sources changed since it was
    written, so the cached shape no longer maps 1:1 onto today's scrapers)."""
    row = db.get(ScrapeCache, query_norm)
    if row is None or row.updated_at is None:
        return None
    if row.updated_at < datetime.utcnow() - _SCRAPE_CACHE_TTL:
        return None
    payload = row.payload or {}
    if list(payload.get("scrapers") or []) != list(scrapers_sig):
        return None
    results = payload.get("results")
    if not isinstance(results, list):
        return None
    # `probed` is stored as lists; restore the (ats, slug, n, tags) tuple shape
    # the cursor/tag write-back expects.
    probed = [
        tuple(p) for p in (payload.get("probed") or [])
        if isinstance(p, (list, tuple)) and len(p) == 4
    ]
    return results, probed


def put_scrape_cache(
    db: Session, query_norm: str, scrapers_sig: list[str],
    results_per_scraper: list, probed: list,
) -> None:
    """Store this hunt's raw scrape output for the next hunter (upsert). Skips
    oversized payloads. Raises on a DB/serialization error — the caller wraps
    this best-effort (a cache write must never break a hunt)."""
    if sum(len(r) for r in results_per_scraper) > _SCRAPE_CACHE_MAX_LEADS:
        return
    payload = {
        "scrapers": list(scrapers_sig),
        "results":  [list(r) for r in results_per_scraper],
        "probed":   [list(p) for p in probed],
    }
    row = db.get(ScrapeCache, query_norm)
    if row is None:
        row = ScrapeCache(query_norm=query_norm)
        db.add(row)
    row.payload = payload
    row.updated_at = datetime.utcnow()
    db.commit()


def get_all_company_tags(db: Session) -> dict[tuple[str, str], list[str]]:
    """Every (ats, slug) -> tech tags, one SELECT. Loaded per hunt to refresh
    the in-memory overlay that ranks ATS probe targets by query relevance."""
    return {
        (r.ats, r.slug): list(r.tags or [])
        for r in db.query(CompanyTag).all()
    }


def upsert_company_tags(db: Session, ats: str, slug: str, tags: list[str]) -> None:
    """Merge newly observed tags for a board (upsert, best-effort)."""
    if not tags:
        return
    row = db.get(CompanyTag, (ats, slug))
    if row is None:
        row = CompanyTag(ats=ats, slug=slug, tags=sorted(set(tags)))
        db.add(row)
    else:
        merged = sorted(set(row.tags or []) | set(tags))
        if merged == list(row.tags or []):
            return
        row.tags = merged
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


def record_domain_patterns(db: Session, items: list[tuple[str, str, bool]]) -> None:
    """Batched record_domain_pattern: ONE existence query + ONE commit for a whole
    hunt's worth of learned patterns, instead of O(N) per-domain round-trips in the
    unbudgeted post-resolve tail (the same class of bottleneck as the per-row
    contact persist). Same upsert semantics. Deduped by domain, a verified
    observation winning over an unverified one. Best-effort — never breaks a hunt."""
    by_domain: dict[str, tuple[str, bool]] = {}
    for domain, pattern, verified in items:
        domain = (domain or "").lower().strip()
        pattern = (pattern or "").strip()
        if not (domain and pattern):
            continue
        prev = by_domain.get(domain)
        # First writer wins for a domain, unless a later one is verified and it
        # wasn't — verified evidence is allowed to take over.
        if prev is None or (verified and not prev[1]):
            by_domain[domain] = (pattern, verified)
    if not by_domain:
        return
    try:
        existing = {
            r.domain: r for r in db.query(EmailPattern)
            .filter(EmailPattern.domain.in_(list(by_domain.keys()))).all()
        }
        for domain, (pattern, verified) in by_domain.items():
            row = existing.get(domain)
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
