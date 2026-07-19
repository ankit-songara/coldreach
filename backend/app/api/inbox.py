"""
Reply detection.

POST /api/inbox/sync
  Scans the user's Gmail inbox for messages FROM any contact we're awaiting a
  reply from, and marks that contact status='replied' + replied_at (or
  'bounced' for delivery failures).

Connection precedence mirrors sending: explicit request creds (IMAP) → stored
OAuth grant (Gmail REST API) → stored App Password (IMAP). The App Password
path needs Gmail IMAP enabled (it is, by default, for App-Password accounts).
"""

import re
import imaplib
import email
import logging
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.database import get_db
from app.db.crud import (
    ContactRepository, ConfigRepository, ReplyRepository, record_pattern_bounce,
)
from app.db.models import User
from app.deps import get_current_user
from app.schemas.contact import ContactUpdate
from app.timeutil import utcnow, to_naive_utc
from app import gmail_oauth
from app.api.send import RECONNECT_MSG

log = logging.getLogger(__name__)
router = APIRouter(prefix="/inbox", tags=["inbox"])

IMAP_HOST = "imap.gmail.com"
AWAITING_STATUSES = {"emailed", "followed_up"}


class InboxSyncRequest(BaseModel):
    # Optional: when empty, the server-stored (encrypted) creds are used.
    gmail_address:      str = ""
    gmail_app_password: str = ""


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


# Cap on the stored reply excerpt — enough for an inbox preview card.
_SNIPPET_CHARS = 400


def _decode_subject(raw: str | None) -> str:
    """Decode an RFC 2047 Subject header to a plain string (best-effort)."""
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))[:500]
    except Exception:
        return raw[:500]


def _reply_snippet(msg) -> str:
    """First ~400 chars of the reply's plain-text body, whitespace-normalized."""
    text = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    text = payload.decode(errors="ignore")
                    break
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            text = payload.decode(errors="ignore")
    return " ".join(text.split())[:_SNIPPET_CHARS]


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

    # Connection precedence: explicit request creds (IMAP) → stored OAuth
    # grant (Gmail API, preferred) → stored App Password (IMAP).
    address, password = req.gmail_address.strip(), req.gmail_app_password
    if not (address and password):
        cfg = ConfigRepository(db, user.id)
        oauth_address, oauth_refresh = cfg.get_gmail_oauth()
        if oauth_address and oauth_refresh:
            return _sync_via_gmail_api(
                db, user, oauth_refresh, awaiting, since_date, contact_repo)
        address, password = cfg.get_gmail_creds()
    if not (address and password):
        raise HTTPException(400,
            "Gmail isn't connected. Add your Gmail address and App Password in Setup first.")

    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST)
        imap.login(address, password)
    except imaplib.IMAP4.error:
        # 400, not 401 — 401 would force-log-out the ColdReach session.
        raise HTTPException(400,
            "Gmail IMAP login failed. Use your App Password (not your normal "
            "password), and make sure IMAP is enabled in Gmail settings.")
    except Exception as e:
        log.warning(f"Gmail IMAP connect failed: {e}")
        raise HTTPException(502, "Couldn't connect to Gmail right now. Please try again in a moment.")

    awaiting_emails = set(awaiting.keys())
    sender_emails: set[str] = set()     # genuine replies
    bounced_emails: set[str] = set()    # addresses named in bounce reports
    # Captured reply content per sender, for the Replies inbox. When a sender
    # replied more than once in the window, the EARLIEST message wins — that's
    # the actual reply to our outreach; later ones are thread follow-ups.
    reply_details: dict[str, dict] = {}
    try:
        imap.select("INBOX", readonly=True)
        typ, data = imap.search(None, f'(SINCE {since_str})')
        if typ == "OK" and data and data[0]:
            uids = data[0].split()
            for uid in uids:
                # Cheap first pass: just the From + Date + Subject headers
                typ, msg_data = imap.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (FROM DATE SUBJECT)])")
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
                        cur = reply_details.get(addr)
                        if cur is None or (
                            msg_dt is not None
                            and (cur["received_at"] is None or msg_dt < cur["received_at"])
                        ):
                            # Fetch the body only for messages we're keeping.
                            snippet = ""
                            typ, full = imap.fetch(uid, "(BODY.PEEK[])")
                            if typ == "OK" and full and full[0] and full[0][1]:
                                snippet = _reply_snippet(email.message_from_bytes(full[0][1]))
                            reply_details[addr] = {
                                "subject":     _decode_subject(hdr.get("Subject")),
                                "snippet":     snippet,
                                "received_at": msg_dt,
                            }
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    now = utcnow()
    hits: list[ReplyHit] = []

    # Replies win over bounces if somehow both appear
    reply_repo = ReplyRepository(db, user.id)
    reply_addrs = (sender_emails & awaiting_emails) - bounced_emails
    for addr in reply_addrs:
        c = awaiting[addr]
        contact_repo.update(c.id, ContactUpdate(status="replied", replied_at=now))
        # Persist the reply content for the Replies inbox. Deduped on
        # (contact, received_at) so re-syncs are no-ops; a message with no
        # parseable Date header falls back to sync time.
        det = reply_details.get(addr) or {}
        reply_repo.add_if_new(
            contact_id=c.id,
            subject=det.get("subject") or "",
            snippet=det.get("snippet") or "",
            received_at=det.get("received_at") or now,
        )
        hits.append(ReplyHit(contact_id=c.id, name=c.name, email=c.email))
        log.info(f"Reply detected from {c.email} — marked replied")

    for addr in bounced_emails - reply_addrs:
        c = awaiting[addr]
        contact_repo.update(c.id, ContactUpdate(status="bounced", bounced=True))
        # Feed the bounce back into pattern memory: a strike against the stored
        # email format for this domain, so bad patterns demote themselves.
        try:
            record_pattern_bounce(db, c.email)
        except Exception:
            pass
        log.info(f"Bounce detected for {c.email} — marked bounced")

    return InboxSyncResponse(
        scanned=len(awaiting),
        replies_found=len(hits),
        bounces_found=len(bounced_emails - reply_addrs),
        hits=hits,
    )


def _sync_via_gmail_api(db, user, refresh_token, awaiting, since_date, contact_repo) -> InboxSyncResponse:
    """Reply detection over the Gmail REST API — the OAuth-grant path.

    Mirrors the IMAP scan's semantics: per contact, only messages that arrived
    after we emailed them count (contacts with no send timestamp fall back to
    the capped scan window), the EARLIEST qualifying message is the one
    persisted (later ones are thread follow-ups), and detection flips the
    contact to 'replied'. Gmail's own search (from: + after:) enforces the
    cutoff server-side, so there is no daemon-message pass here — bounce
    detection stays on the IMAP path for App Password users.
    """
    try:
        access_token = gmail_oauth.access_token_for(refresh_token)
    except gmail_oauth.GrantRevoked:
        # 400, not 401 — 401 would force-log-out the ColdReach session.
        raise HTTPException(400, RECONNECT_MSG)
    except Exception as e:
        log.warning(f"Gmail OAuth token refresh failed: {e}")
        raise HTTPException(502, "Couldn't connect to Gmail right now. Please try again in a moment.")

    now = utcnow()
    reply_repo = ReplyRepository(db, user.id)
    hits: list[ReplyHit] = []
    for c in awaiting.values():
        cutoff = c.last_emailed_at or since_date          # naive UTC, like the DB
        after_epoch = int(cutoff.replace(tzinfo=timezone.utc).timestamp())
        try:
            msgs = gmail_oauth.find_replies_from(access_token, c.email, after_epoch)
        except gmail_oauth.GrantRevoked:
            raise HTTPException(400, RECONNECT_MSG)
        except Exception as e:
            # One contact's scan failing shouldn't sink the whole sync.
            log.warning(f"Gmail API reply scan failed for {c.email}: {e}")
            continue
        if not msgs:
            continue
        # Oldest first — the first hit is the actual reply to our outreach.
        first = msgs[0]
        contact_repo.update(c.id, ContactUpdate(status="replied", replied_at=now))
        reply_repo.add_if_new(
            contact_id=c.id,
            subject=first.get("subject") or "",
            snippet=first.get("snippet") or "",
            received_at=first.get("received_at") or now,
        )
        hits.append(ReplyHit(contact_id=c.id, name=c.name, email=c.email))
        log.info(f"Reply detected from {c.email} — marked replied (oauth)")

    return InboxSyncResponse(
        scanned=len(awaiting), replies_found=len(hits),
        bounces_found=0, hits=hits,
    )


class ReplyMessageOut(BaseModel):
    id:          int
    contact_id:  int
    name:        str
    company:     str
    designation: str
    status:      str
    subject:     str
    snippet:     str
    received_at: datetime | None


@router.get("/replies", response_model=list[ReplyMessageOut])
def list_replies(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Stored replies captured by sync, newest first, with the contact's current
    identity/status joined in — powers the v2 Replies inbox screen."""
    rows = ReplyRepository(db, user.id).latest_with_contacts(limit=100)
    return [
        ReplyMessageOut(
            id=m.id, contact_id=m.contact_id,
            name=c.name, company=c.company, designation=c.designation,
            status=c.status, subject=m.subject, snippet=m.snippet,
            received_at=m.received_at,
        )
        for m, c in rows
    ]
