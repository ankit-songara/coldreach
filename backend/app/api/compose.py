"""Email composition routes — POST /api/compose and /api/compose/followup."""

import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.crud import (
    ContactRepository, DraftRepository, ResumeRepository,
    resolve_sender_name, resolve_signature_links,
)
from app.db.models import User
from app.deps import get_current_user
from pydantic import BaseModel
from app.schemas.email import ComposeRequest, FollowUpRequest, DraftOut, DraftCreate
from app.llm.generator import generator
from app.llm.parsing import parse_subject_body

log = logging.getLogger(__name__)
router = APIRouter(prefix="/compose", tags=["compose"])


def _friendly_llm_error(e: Exception) -> str:
    """Map raw provider exceptions to a message safe to show an end user."""
    s = str(e).lower()
    if "429" in s or "rate limit" in s or "rate_limit" in s:
        return "The email writer is busy right now (rate limit). Wait a few seconds and try again."
    if "no llm provider" in s or "api key" in s or "authentication" in s or "401" in s:
        return "Email generation isn't set up on this server yet. Contact the administrator."
    if "timeout" in s or "timed out" in s or "connect" in s:
        return "The email writer took too long to respond. Please try again."
    return "Couldn't generate the email this time. Please try again in a moment."


@router.post("", response_model=DraftOut)
async def compose(req: ComposeRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Generate a cold email for a contact using the configured LLM."""
    repo = ContactRepository(db, user.id)
    contact = repo.get_by_id(req.contact_id)
    if not contact:
        raise HTTPException(404, f"Contact {req.contact_id} not found")

    resume_text = req.resume.strip()
    if not resume_text:
        saved = ResumeRepository(db, user.id).get_latest()
        resume_text = saved.text if saved else ""
    if not resume_text:
        raise HTTPException(400, "No résumé provided. Upload one in Setup or include resume text.")

    # Prefer user-supplied context; otherwise fall back to the genuine context
    # we captured at hunt time (HN post, GitHub repos, …).
    company_context = req.company_context.strip() or (contact.context or "")
    sender_name  = resolve_sender_name(db, user.id, user.email)
    sender_links = resolve_signature_links(db, user.id)

    try:
        email_text = await generator.generate(
            name=contact.name,
            designation=contact.designation,
            company=contact.company,
            resume=resume_text,
            company_context=company_context,
            source=contact.source or "",
            sender_name=sender_name,
            sender_links=sender_links,
        )
    except Exception as e:
        # Full details go to the server log only — raw provider errors (rate
        # limits, model names, stack fragments) mean nothing to the end user.
        log.error(f"LLM generation failed for contact {contact.id}: {e}")
        raise HTTPException(502, _friendly_llm_error(e))

    # Parse SUBJECT / BODY
    subject, body = parse_subject_body(email_text)

    draft = DraftRepository(db, user.id).create(DraftCreate(
        contact_id=contact.id,
        subject=subject,
        body=body,
        is_followup=False,
    ))
    return draft


@router.post("/followup", response_model=DraftOut)
async def followup(req: FollowUpRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Generate a follow-up email for a non-responding contact."""
    contact = ContactRepository(db, user.id).get_by_id(req.contact_id)
    if not contact:
        raise HTTPException(404, f"Contact {req.contact_id} not found")

    try:
        email_text = await generator.generate_followup(
            name=contact.name,
            company=contact.company,
            original_email=req.original_email,
            sender_name=resolve_sender_name(db, user.id, user.email),
            sender_links=resolve_signature_links(db, user.id),
            context=contact.context or "",
        )
    except Exception as e:
        log.error(f"Follow-up generation failed for contact {contact.id}: {e}")
        raise HTTPException(502, _friendly_llm_error(e))

    subject, body = parse_subject_body(email_text)

    draft = DraftRepository(db, user.id).create(DraftCreate(
        contact_id=contact.id,
        subject=subject,
        body=body,
        is_followup=True,
    ))
    return draft


class DraftEdit(BaseModel):
    subject: str
    body:    str


@router.put("/draft/{draft_id}", response_model=DraftOut)
def edit_draft(draft_id: int, req: DraftEdit, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Update a draft's subject/body after the user edits it by hand."""
    updated = DraftRepository(db, user.id).update_content(draft_id, req.subject.strip(), req.body.strip())
    if not updated:
        raise HTTPException(404, f"Draft {draft_id} not found")
    return updated


@router.get("/drafts/all", response_model=list[DraftOut])
def get_all_drafts(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """All drafts for the current user in one query. The frontend groups them by
    contact_id client-side — replaces the old one-request-per-contact hydration
    (N contacts = N serverless invocations + N DB sessions)."""
    return DraftRepository(db, user.id).get_all()


@router.get("/{contact_id}", response_model=list[DraftOut])
def get_drafts(contact_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """List all email drafts for a contact."""
    return DraftRepository(db, user.id).get_for_contact(contact_id)
