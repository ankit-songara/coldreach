"""
Profile / signature configuration.

  GET  /api/config          current signature settings
  POST /api/config/profile  set the sender name + signature links

(The server-side Gmail credential store and follow-up scheduler were removed:
on serverless hosts there is no persistent worker to deliver queued mail, so
sending is always driven from the browser session.)
"""

import logging
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.database import get_db
from app.db.crud import ConfigRepository, resolve_sender_name, resolve_signature_links
from app.db.models import User
from app.deps import get_current_user

log = logging.getLogger(__name__)
router = APIRouter(tags=["config"])


class ConfigStatus(BaseModel):
    sender_name:     str   # name used in email greetings/signatures
    signature_links: str   # one line of links under the name (GitHub/LinkedIn/…)


class ProfileRequest(BaseModel):
    sender_name:     str
    signature_links: str | None = None   # None = leave unchanged


@router.get("/config", response_model=ConfigStatus)
def get_config(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
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
    # Both values are RESOLVED (explicit override → résumé auto-detection), so
    # the frontend can render the signature preview without its own fallbacks.
    return ConfigStatus(
        sender_name=resolve_sender_name(db, user.id, user.email),
        signature_links=resolve_signature_links(db, user.id),
    )
