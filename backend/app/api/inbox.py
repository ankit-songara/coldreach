"""
Reply detection via Gmail IMAP.

POST /api/inbox/sync
  Connects to the user's Gmail over IMAP, scans the inbox for messages FROM any
  contact we're awaiting a reply from, and marks that contact
  status='replied' + replied_at (or 'bounced' for delivery failures).

Uses the same Gmail address + App Password as sending — no extra setup.
Gmail IMAP must be enabled (it is, by default, for App-Password accounts).
"""

import re
import imaplib
import email
import logging
from datetime import timedelta
from email.utils import parseaddr, parsedate_to_datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.database import get_db
from app.db.crud import ContactRepository
from app.db.models import User
from app.deps import get_current_user
from app.schemas.contact import ContactUpdate
from app.timeutil import utcnow, to_naive_utc

log = logging.getLogger(__name__)
router = APIRouter(prefix="/inbox", tags=["inbox"])

IMAP_HOST = "imap.gmail.com"
AWAITING_STATUSES = {"emailed", "followed_up"}


class InboxSyncRequest(BaseModel):
    gmail_address:      str
    gmail_app_password: str


class ReplyHit(BaseModel):
    contact_id: int
    name:       str
    email:      str


class InboxSyncResponse(BaseModel):
    scanned:       int          # contacts we were awaiting a reply from
    replies_found: int
    bounces_found: int
    hits:          list[ReplyHit]


# Senders that indicate a bounce / non-delivery report
_DAEMON_HINTS = ("mailer-daemon", "postmaster", "mail delivery subsystem")


def _is_daemon(addr: str) -> bool:
    a = addr.lower()
    return any(h in a for h in _DAEMON_HINTS)


def _parse_date(raw: str | None):
    """Parse an email Date header to naive UTC, or None if absent/unparseable."""
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        return to_naive_utc(dt) if dt else None
    except Exception:
        return None


# "Final-Recipient: rfc822; someone@example.com" (per RFC 3464)
_FINAL_RECIPIENT_RE = re.compile(
    r"^(?:Final|Original)-Recipient:\s*[^;]+;\s*(\S+)", re.IGNORECASE | re.MULTILINE
)


def _bounced_recipients(msg) -> set[str]:
    """
    Extract the addresses a bounce/NDR is reporting on.

    Prefer the structured message/delivery-status part (RFC 3464 Final-Recipient
    headers); this is far more precise than scanning the human-readable body,
    which may mention several unrelated addresses.
    """
    found: set[str] = set()
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "message/delivery-status":
                payload = part.get_payload(decode=True)
                if payload:
                    text = payload.decode(errors="ignore")
                    for m in _FINAL_RECIPIENT_RE.finditer(text):
                        _, addr = parseaddr(m.group(1))
                        addr = (addr or m.group(1)).strip().strip("<>").lower()
                        if "@" in addr:
                            found.add(addr)
    return found


def _body_text(msg) -> str:
    """Best-effort plain-text extraction of a message body."""
    parts = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() in ("text/plain", "message/delivery-status"):
                payload = part.get_payload(decode=True)
                if payload:
                    parts.append(payload.decode(errors="ignore"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            parts.append(payload.decode(errors="ignore"))
    return "\n".join(parts).lower()


@router.post("/sync", response_model=InboxSyncResponse)
def sync_inbox(req: InboxSyncRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    contact_repo = ContactRepository(db, user.id)

    # Contacts we're awaiting a reply from, keyed by their (lowercased) email
    awaiting = {
        c.email.lower(): c
        for c in contact_repo.get_all()
        if c.status in AWAITING_STATUSES and not c.replied_at
    }
    if not awaiting:
        return InboxSyncResponse(scanned=0, replies_found=0, bounces_found=0, hits=[])

    # Only scan back as far as the oldest email we sent (cap at 90 days)
    sent_times = [c.last_emailed_at for c in awaiting.values() if c.last_emailed_at]
    since_date = min(sent_times) if sent_times else utcnow() - timedelta(days=90)
    since_date = max(since_date, utcnow() - timedelta(days=90))
    since_str  = since_date.strftime("%d-%b-%Y")

    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST)
        imap.login(req.gmail_address, req.gmail_app_password)
    except imaplib.IMAP4.error:
        raise HTTPException(401,
            "Gmail IMAP login failed. Use your App Password (not your normal "
            "password), and make sure IMAP is enabled in Gmail settings.")
    except Exception as e:
        raise HTTPException(502, f"Could not connect to Gmail IMAP: {e}")

    awaiting_emails = set(awaiting.keys())
    sender_emails: set[str] = set()     # genuine replies
    bounced_emails: set[str] = set()    # addresses named in bounce reports
    try:
        imap.select("INBOX", readonly=True)
        typ, data = imap.search(None, f'(SINCE {since_str})')
        if typ == "OK" and data and data[0]:
            uids = data[0].split()
            for uid in uids:
                # Cheap first pass: just the From + Date headers
                typ, msg_data = imap.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (FROM DATE)])")
                if typ != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                if not raw:
                    continue
                hdr = email.message_from_bytes(raw)
                _, addr = parseaddr(hdr.get("From", ""))
                if not addr:
                    continue
                addr = addr.lower()

                if _is_daemon(addr):
                    # Bounce/NDR — fetch the full message and find which of our
                    # awaited addresses it's reporting on.
                    typ, full = imap.fetch(uid, "(BODY.PEEK[])")
                    if typ == "OK" and full and full[0]:
                        full_msg = email.message_from_bytes(full[0][1])
                        # Prefer the structured Final-Recipient headers...
                        reported = _bounced_recipients(full_msg) & awaiting_emails
                        if reported:
                            bounced_emails |= reported
                        else:
                            # ...fall back to scanning the body only if needed.
                            body = _body_text(full_msg)
                            for awaited in awaiting_emails:
                                if awaited in body:
                                    bounced_emails.add(awaited)
                elif addr in awaiting:
                    # Only count it as a reply if it actually arrived after we
                    # emailed them — a pre-existing message from this person (e.g.
                    # an earlier thread) is not a reply to our outreach.
                    msg_dt = _parse_date(hdr.get("Date"))
                    cutoff = awaiting[addr].last_emailed_at
                    if cutoff is None or msg_dt is None or msg_dt >= cutoff:
                        sender_emails.add(addr)
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    now = utcnow()
    hits: list[ReplyHit] = []

    # Replies win over bounces if somehow both appear
    reply_addrs = (sender_emails & awaiting_emails) - bounced_emails
    for addr in reply_addrs:
        c = awaiting[addr]
        contact_repo.update(c.id, ContactUpdate(status="replied", replied_at=now))
        hits.append(ReplyHit(contact_id=c.id, name=c.name, email=c.email))
        log.info(f"Reply detected from {c.email} — marked replied")

    for addr in bounced_emails - reply_addrs:
        c = awaiting[addr]
        contact_repo.update(c.id, ContactUpdate(status="bounced", bounced=True))
        log.info(f"Bounce detected for {c.email} — marked bounced")

    return InboxSyncResponse(
        scanned=len(awaiting),
        replies_found=len(hits),
        bounces_found=len(bounced_emails - reply_addrs),
        hits=hits,
    )
