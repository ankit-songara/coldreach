"""
Automation API — server-side Gmail config + follow-up sequence scheduling.

  POST   /api/config/gmail      save Gmail creds server-side (enables automation)
  GET    /api/config            automation status (no secrets returned)
  POST   /api/config/automation toggle automation on/off, set daily cap

  POST   /api/followups/schedule  queue follow-ups for contacts after N days
  GET    /api/followups           list pending scheduled follow-ups
  DELETE /api/followups/{id}      cancel a pending follow-up
"""

import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.database import get_db
from app.db.crud import (
    ConfigRepository, ScheduledEmailRepository, ContactRepository, DraftRepository,
    resolve_sender_name,
)
from app.db.models import User
from app.deps import get_current_user
from app import mailer
from app.llm.generator import generator
from app.llm.parsing import parse_subject_body

log = logging.getLogger(__name__)
router = APIRouter(tags=["automation"])


# ── Config ────────────────────────────────────────────────────────────────────
class GmailConfigRequest(BaseModel):
    gmail_address:      str
    gmail_app_password: str


class AutomationToggle(BaseModel):
    enabled:        bool | None = None
    daily_send_cap: int | None  = None


class ConfigStatus(BaseModel):
    gmail_address:      str
    has_credentials:    bool
    automation_enabled: bool
    daily_send_cap:     int
    sender_name:        str   # name used in email greetings/signatures
    signature_links:    str   # one line of links under the name (GitHub/LinkedIn/…)


class ProfileRequest(BaseModel):
    sender_name:     str
    signature_links: str | None = None   # None = leave unchanged


@router.post("/config/gmail", response_model=ConfigStatus)
def save_gmail_config(req: GmailConfigRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Verify and persist Gmail credentials server-side for automated sending."""
    try:
        mailer.verify_credentials(req.gmail_address, req.gmail_app_password)
    except Exception as e:
        raise HTTPException(401, f"Gmail verification failed: {e}")

    cfg = ConfigRepository(db, user.id)
    cfg.set("gmail_address", req.gmail_address)
    cfg.set("gmail_app_password", req.gmail_app_password)
    return _status(db, user)


@router.get("/config", response_model=ConfigStatus)
def get_config(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _status(db, user)


@router.post("/config/automation", response_model=ConfigStatus)
def set_automation(req: AutomationToggle, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    cfg = ConfigRepository(db, user.id)
    if req.enabled is not None:
        addr, pw = cfg.get_gmail_creds()
        if req.enabled and not (addr and pw):
            raise HTTPException(400, "Save Gmail credentials before enabling automation.")
        cfg.set("automation_enabled", "true" if req.enabled else "false")
    if req.daily_send_cap is not None:
        cfg.set("daily_send_cap", str(max(1, min(500, req.daily_send_cap))))
    return _status(db, user)


@router.post("/config/profile", response_model=ConfigStatus)
def set_profile(req: ProfileRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Set the signature: name (overrides résumé auto-detection) and link line."""
    cfg = ConfigRepository(db, user.id)
    cfg.set("sender_name", req.sender_name.strip())
    if req.signature_links is not None:
        # One line, whitespace-collapsed, capped — it renders under the name.
        cfg.set("signature_links", " ".join(req.signature_links.split())[:200])
    return _status(db, user)


def _status(db: Session, user: User) -> ConfigStatus:
    cfg = ConfigRepository(db, user.id)
    addr, pw = cfg.get_gmail_creds()
    return ConfigStatus(
        gmail_address=addr,
        has_credentials=bool(addr and pw),
        automation_enabled=cfg.automation_enabled(),
        daily_send_cap=int(cfg.get("daily_send_cap", "50") or 50),
        sender_name=resolve_sender_name(db, user.id, user.email),
        signature_links=cfg.get("signature_links", ""),
    )


# ── Follow-up sequences ───────────────────────────────────────────────────────
class ScheduleFollowupsRequest(BaseModel):
    contact_ids: list[int] = []     # empty = all emailed-but-unreplied contacts
    days:        int       = 3      # delay before the nudge


class ScheduledItem(BaseModel):
    id:          int
    contact_id:  int
    name:        str
    email:       str
    subject:     str
    send_at:     datetime
    is_followup: bool

    model_config = {"from_attributes": True}


class ScheduleFollowupsResponse(BaseModel):
    scheduled: int
    skipped:   int
    items:     list[ScheduledItem]


@router.post("/followups/schedule", response_model=ScheduleFollowupsResponse)
async def schedule_followups(req: ScheduleFollowupsRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Generate and queue a follow-up for each eligible contact, send_at = now + days."""
    contacts_repo = ContactRepository(db, user.id)
    drafts_repo   = DraftRepository(db, user.id)
    sched_repo    = ScheduledEmailRepository(db, user.id)

    if req.contact_ids:
        targets = [contacts_repo.get_by_id(cid) for cid in req.contact_ids]
        targets = [c for c in targets if c]
    else:
        targets = [c for c in contacts_repo.get_all() if c.status == "emailed"]

    send_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=max(0, req.days))
    sender_name  = resolve_sender_name(db, user.id, user.email)
    sender_links = ConfigRepository(db, user.id).get("signature_links", "")
    items, skipped = [], 0

    for contact in targets:
        # Eligibility: emailed, not replied, no pending follow-up already queued
        if contact.replied_at or contact.status not in ("emailed", "followed_up"):
            skipped += 1
            continue
        if sched_repo.pending_for_contact(contact.id):
            skipped += 1
            continue

        original = next((d for d in drafts_repo.get_for_contact(contact.id) if not d.is_followup), None)
        if not original:
            skipped += 1
            continue

        try:
            text = await generator.generate_followup(
                name=contact.name,
                company=contact.company,
                original_email=f"SUBJECT: {original.subject}\n\nBODY:\n{original.body}",
                sender_name=sender_name,
                sender_links=sender_links,
                context=contact.context or "",
            )
            subject, body = parse_subject_body(text, fallback_subject=f"Re: {original.subject}")
        except Exception as e:
            log.error(f"Follow-up generation failed for {contact.email}: {e}")
            skipped += 1
            continue

        item = sched_repo.create(contact.id, subject, body, send_at, is_followup=True)
        items.append(ScheduledItem(
            id=item.id, contact_id=contact.id, name=contact.name, email=contact.email,
            subject=subject, send_at=send_at, is_followup=True,
        ))

    return ScheduleFollowupsResponse(scheduled=len(items), skipped=skipped, items=items)


@router.get("/followups", response_model=list[ScheduledItem])
def list_followups(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sched_repo    = ScheduledEmailRepository(db, user.id)
    contacts_repo = ContactRepository(db, user.id)
    out = []
    for item in sched_repo.all_pending():
        c = contacts_repo.get_by_id(item.contact_id)
        if not c:
            continue
        out.append(ScheduledItem(
            id=item.id, contact_id=item.contact_id, name=c.name, email=c.email,
            subject=item.subject, send_at=item.send_at, is_followup=item.is_followup,
        ))
    return out


@router.delete("/followups/{item_id}")
def cancel_followup(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not ScheduledEmailRepository(db, user.id).cancel(item_id):
        raise HTTPException(404, "No pending follow-up with that id")
    return {"ok": True}
