"""
Email verification API.

  POST /api/verify   verify a set of contacts (empty = all) and persist the
                     verdict to contact.email_status. Returns a summary so the
                     UI can warn before sending to invalid addresses.
"""

import asyncio
import logging
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.database import get_db
from app.db.crud import ContactRepository
from app.db.models import User
from app.deps import get_current_user
from app.schemas.contact import ContactUpdate
from app.verifier import verify_email, verify_with_hunter
from app.config import settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/verify", tags=["verify"])


class VerifyRequest(BaseModel):
    contact_ids: list[int] = []   # empty = verify everything not yet verified


class VerifyResult(BaseModel):
    contact_id:   int
    email:        str
    email_status: str             # valid | risky | invalid


class VerifyResponse(BaseModel):
    valid:   int
    risky:   int
    invalid: int
    results: list[VerifyResult]


@router.post("", response_model=VerifyResponse)
async def verify_contacts(req: VerifyRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    repo = ContactRepository(db, user.id)
    if req.contact_ids:
        contacts = [c for c in (repo.get_by_id(i) for i in req.contact_ids) if c]
    else:
        contacts = [c for c in repo.get_all() if c.email_status == "unknown"]

    # When a Hunter key is configured, use its real deliverability check; otherwise
    # fall back to the local heuristic (syntax + MX + disposable/role). Bounded
    # concurrency keeps both Hunter's rate limit and DNS load in check.
    hunter_key = (settings.hunter_api_key or "").strip()
    sem = asyncio.Semaphore(5)

    async def verdict_for(email: str) -> str:
        async with sem:
            if hunter_key:
                v = await verify_with_hunter(email, hunter_key)
                if v:
                    return v
            return await asyncio.to_thread(verify_email, email)

    verdicts = await asyncio.gather(*(verdict_for(c.email) for c in contacts))

    results: list[VerifyResult] = []
    for contact, verdict in zip(contacts, verdicts):
        repo.update(contact.id, ContactUpdate(email_status=verdict))
        results.append(VerifyResult(contact_id=contact.id, email=contact.email, email_status=verdict))

    return VerifyResponse(
        valid=sum(1 for r in results if r.email_status == "valid"),
        risky=sum(1 for r in results if r.email_status == "risky"),
        invalid=sum(1 for r in results if r.email_status == "invalid"),
        results=results,
    )
