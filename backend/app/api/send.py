"""
Bulk email sending via Gmail SMTP.
Uses the user's Gmail address + App Password (no OAuth needed).

How to get an App Password:
  1. Enable 2-Step Verification on your Google account
  2. Go to myaccount.google.com/apppasswords
  3. Create a new app password — use that 16-char string here
"""

import os
import time
import random
import smtplib
import logging
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.database import get_db
from app.db.crud import (
    ContactRepository, DraftRepository, ConfigRepository,
    already_first_touched,
)
from app.db.models import User
from app.deps import get_current_user
from app.schemas.contact import ContactUpdate
from app.mailer import normalize_app_password

log = logging.getLogger(__name__)
router = APIRouter(prefix="/send", tags=["send"])

# Serverless functions have a hard wall-clock limit, so the human-like pauses
# between sends must shrink to fit. The frontend compensates by sending in
# small chunks (one request per chunk) instead of one giant request.
_SERVERLESS = bool(os.environ.get("VERCEL"))


class BulkSendRequest(BaseModel):
    contact_ids:        list[int] = []   # empty = all contacts that have drafts
    gmail_address:      str
    gmail_app_password: str


class SendResult(BaseModel):
    contact_id:   int
    name:         str
    email:        str
    status:       str          # "sent" | "failed"
    error:        str = ""


class BulkSendResponse(BaseModel):
    sent:     int
    failed:   int
    deferred: int = 0      # held back by the daily cap — try again tomorrow
    results:  list[SendResult]


@router.post("/bulk", response_model=BulkSendResponse)
def bulk_send(req: BulkSendRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Send all drafted emails via Gmail SMTP in one shot."""
    contact_repo = ContactRepository(db, user.id)
    draft_repo   = DraftRepository(db, user.id)

    # Resolve contacts to send
    if req.contact_ids:
        contacts = [contact_repo.get_by_id(cid) for cid in req.contact_ids]
        contacts = [c for c in contacts if c]
    else:
        contacts = contact_repo.get_all()

    # Build send queue: contacts with a draft that haven't been emailed yet.
    # Skip addresses already known to be invalid or bounced — protects the
    # sending account's reputation.
    queue = []
    skipped_bad = 0
    for contact in contacts:
        # Never re-send a first-touch to anyone already emailed (in any later
        # state: emailed / followed_up / replied / interview / rejected).
        if already_first_touched(contact):
            continue
        if contact.bounced or contact.email_status == "invalid":
            skipped_bad += 1
            continue
        drafts = draft_repo.get_for_contact(contact.id)
        draft  = next((d for d in drafts if not d.is_followup), None)
        if draft:
            queue.append((contact, draft))

    if skipped_bad:
        log.info(f"Skipped {skipped_bad} invalid/bounced addresses")

    if not queue:
        raise HTTPException(400, "No contacts with drafts found.")

    # ── Daily cap ────────────────────────────────────────────────────────────
    # Gmail throttles/flags accounts that fire many cold emails. Cap per rolling
    # 24h (configurable) and defer the overflow rather than risk a suspension.
    cfg = ConfigRepository(db, user.id)
    daily_cap = int(cfg.get("daily_send_cap", "50") or 50)
    since_24h = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)
    sent_last_24h = contact_repo.count_emailed_since(since_24h)
    budget = max(0, daily_cap - sent_last_24h)
    deferred = 0
    if len(queue) > budget:
        deferred = len(queue) - budget
        queue = queue[:budget]
        log.info(f"Daily cap {daily_cap}: sending {len(queue)}, deferring {deferred}")
    if not queue:
        raise HTTPException(429,
            f"You've hit today's sending limit of {daily_cap} emails — it protects your "
            f"Gmail account from being flagged. {deferred} emails are waiting; try again tomorrow.")

    # Normalize once: Gmail App Passwords are often pasted with the display spaces.
    gmail_address = req.gmail_address.strip()
    gmail_app_password = normalize_app_password(req.gmail_app_password)

    # Verify credentials once before sending anything
    try:
        test_smtp = smtplib.SMTP("smtp.gmail.com", 587, timeout=15)
        test_smtp.starttls()
        test_smtp.login(gmail_address, gmail_app_password)
        test_smtp.quit()
    except smtplib.SMTPAuthenticationError:
        raise HTTPException(401,
            "Gmail authentication failed. Check your address and App Password. "
            "Make sure 2-Step Verification is on and you're using an App Password "
            "(not your regular Gmail password)."
        )
    except Exception as e:
        raise HTTPException(502, f"Could not connect to Gmail SMTP: {e}")

    def send_batch(batch) -> list[SendResult]:
        """Open ONE authenticated SMTP connection and reuse it for the batch.

        Re-logging in per message is slow and is itself a pattern Gmail flags;
        one login per small batch keeps concurrency low and traffic human-like.
        """
        out: list[SendResult] = []
        smtp = None
        try:
            smtp = smtplib.SMTP("smtp.gmail.com", 587, timeout=20)
            smtp.starttls()
            smtp.login(gmail_address, gmail_app_password)
        except Exception as e:
            # Whole batch fails if we can't establish the session
            log.error(f"SMTP session failed for batch: {e}")
            for contact, _ in batch:
                out.append(SendResult(contact_id=contact.id, name=contact.name,
                                      email=contact.email, status="failed", error=str(e)))
            return out

        try:
            for contact, draft in batch:
                try:
                    # Tiny human-like jitter — trimmed on serverless to stay
                    # inside the function's execution limit.
                    time.sleep(random.uniform(0.1, 0.4) if _SERVERLESS else random.uniform(0.2, 1.2))
                    msg = MIMEMultipart("alternative")
                    msg["From"]    = gmail_address
                    msg["To"]      = contact.email
                    msg["Subject"] = draft.subject
                    msg.attach(MIMEText(draft.body, "plain"))
                    smtp.sendmail(gmail_address, contact.email, msg.as_string())
                    log.info(f"Sent to {contact.email}")
                    out.append(SendResult(contact_id=contact.id, name=contact.name,
                                          email=contact.email, status="sent"))
                except Exception as e:
                    log.error(f"Failed {contact.email}: {e}")
                    out.append(SendResult(contact_id=contact.id, name=contact.name,
                                          email=contact.email, status="failed", error=str(e)))
        finally:
            try:
                smtp.quit()
            except Exception:
                pass
        return out

    # Send in small batches with a randomized pause between them. Jitter makes
    # the traffic look less machine-like (constant intervals are a spam signal)
    # and keeps concurrency low so Gmail doesn't throttle.
    results: list[SendResult] = []
    batch_size = 3
    batches = [queue[i:i+batch_size] for i in range(0, len(queue), batch_size)]

    for batch_idx, batch in enumerate(batches):
        if batch_idx > 0:
            time.sleep(random.uniform(0.5, 1.0) if _SERVERLESS else random.uniform(1.5, 4.0))

        batch_results = send_batch(batch)
        for result in batch_results:
            results.append(result)
            if result.status == "sent":
                contact_repo.update(result.contact_id, ContactUpdate(
                    status="emailed", last_emailed_at=datetime.now(timezone.utc).replace(tzinfo=None)))

        log.info(f"Batch {batch_idx+1}/{len(batches)} done — {len(results)} total so far")

    sent   = sum(1 for r in results if r.status == "sent")
    failed = sum(1 for r in results if r.status == "failed")

    return BulkSendResponse(sent=sent, failed=failed, deferred=deferred, results=results)


@router.post("/test")
def test_connection(req: BulkSendRequest, user: User = Depends(get_current_user)):
    """Verify Gmail credentials without sending any email."""
    try:
        smtp = smtplib.SMTP("smtp.gmail.com", 587, timeout=10)
        smtp.starttls()
        smtp.login(req.gmail_address.strip(), normalize_app_password(req.gmail_app_password))
        smtp.quit()
        return {"ok": True, "message": "Gmail connection successful"}
    except smtplib.SMTPAuthenticationError as e:
        # Surface Gmail's actual response so we can distinguish causes:
        #   535-5.7.8  Username and Password not accepted  → wrong/invalid App Password
        #   534-5.7.14 / 5.7.9  Please log in via your web browser → IP/security block
        #                       (common when sending from a datacenter IP like Vercel)
        code = getattr(e, "smtp_code", "?")
        raw = getattr(e, "smtp_error", b"")
        detail = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        detail = " ".join(detail.split())
        log.warning(f"Gmail auth rejected [{code}]: {detail}")
        if "5.7.14" in detail or "5.7.9" in detail or "web browser" in detail.lower():
            raise HTTPException(401,
                f"Gmail blocked this login from the server's IP [{code}]. Your App "
                f"Password is likely fine — Gmail distrusts logins from cloud/datacenter "
                f"IPs. Gmail said: {detail}"
            )
        raise HTTPException(401,
            f"Gmail rejected the credentials [{code}]. Make sure 2-Step Verification "
            f"is ON and you pasted a 16-char App Password (not your normal password). "
            f"Gmail said: {detail}"
        )
    except Exception as e:
        raise HTTPException(502, f"Could not reach Gmail SMTP: {e}")
