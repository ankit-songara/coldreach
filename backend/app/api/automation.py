"""
Profile / signature / Gmail-connection configuration.

  GET    /api/config          signature + gmail connection status (no secrets)
  POST   /api/config/profile  set the sender name + signature links
  POST   /api/config/gmail    verify and store Gmail creds (password encrypted)
  DELETE /api/config/gmail    forget stored Gmail creds

The App Password is Fernet-encrypted with SECRET_KEY before it touches the
database and is never returned by any endpoint. Send/inbox routes fall back to
these stored creds when a request doesn't carry its own.
"""

import logging
import smtplib
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.database import get_db
from app.db.crud import ConfigRepository, resolve_sender_name, resolve_signature_links
from app.db.models import User
from app.deps import get_current_user
from app import mailer

log = logging.getLogger(__name__)
router = APIRouter(tags=["config"])


class ConfigStatus(BaseModel):
    sender_name:     str   # name used in email greetings/signatures
    signature_links: str   # one line of links under the name (GitHub/LinkedIn/…)
    gmail_address:   str = ""     # stored sending address ('' = not connected)
    has_gmail:       bool = False  # true when creds are stored server-side


class ProfileRequest(BaseModel):
    sender_name:     str
    signature_links: str | None = None   # None = leave unchanged


class GmailConfigRequest(BaseModel):
    gmail_address:      str
    gmail_app_password: str


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


@router.post("/config/gmail", response_model=ConfigStatus)
def save_gmail(req: GmailConfigRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Verify the credentials against Gmail, then store them (password encrypted)."""
    address = req.gmail_address.strip()
    app_password = mailer.normalize_app_password(req.gmail_app_password)
    if not address or not app_password:
        raise HTTPException(400, "Enter your Gmail address and App Password.")
    try:
        mailer.verify_credentials(address, app_password)
    except smtplib.SMTPAuthenticationError:
        raise HTTPException(401,
            "Gmail rejected the credentials. Make sure 2-Step Verification is ON "
            "and you pasted a 16-character App Password (not your normal password).")
    except Exception as e:
        log.warning(f"Gmail verify failed for user {user.id}: {e}")
        raise HTTPException(502, "Couldn't reach Gmail to verify. Try again in a moment.")

    cfg = ConfigRepository(db, user.id)
    cfg.set("gmail_address", address)
    cfg.set("gmail_app_password", app_password)
    log.info(f"[user {user.id}] Gmail connected ({address})")
    return _status(db, user)


@router.delete("/config/gmail", response_model=ConfigStatus)
def delete_gmail(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Forget the stored Gmail credentials."""
    cfg = ConfigRepository(db, user.id)
    cfg.set("gmail_address", "")
    cfg.set("gmail_app_password", "")
    log.info(f"[user {user.id}] Gmail disconnected")
    return _status(db, user)


def _status(db: Session, user: User) -> ConfigStatus:
    # Signature values are RESOLVED (explicit override → résumé auto-detection),
    # so the frontend can render the preview without its own fallbacks.
    cfg = ConfigRepository(db, user.id)
    addr, pw = cfg.get_gmail_creds()
    return ConfigStatus(
        sender_name=resolve_sender_name(db, user.id, user.email),
        signature_links=resolve_signature_links(db, user.id),
        gmail_address=addr,
        has_gmail=bool(addr and pw),
    )
