"""
Profile / signature / Gmail-connection configuration.

  GET    /api/config          signature + gmail connection status (no secrets)
  POST   /api/config/profile  set the sender name + signature links
  POST   /api/config/gmail    verify and store Gmail creds (password encrypted)
  DELETE /api/config/gmail    forget stored Gmail creds

  GET    /api/config/gmail/oauth/start     one-click connect: consent URL
  POST   /api/config/gmail/oauth/complete  authenticated completion (SPA posts
                                           Google's ?code&state with its token)
  DELETE /api/config/gmail/oauth           disconnect + best-effort revoke

Secrets (App Password, OAuth refresh token) are Fernet-encrypted with
SECRET_KEY before they touch the database and are never returned by any
endpoint. Send/inbox prefer the OAuth grant when both are present.
"""

import logging
import smtplib
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.database import get_db
from app.db.crud import (
    ConfigRepository, ResumeRepository, resolve_sender_name, resolve_signature_links,
)
from app.db.models import User
from app.deps import get_current_user
from app import mailer
from app import gmail_oauth

log = logging.getLogger(__name__)
router = APIRouter(tags=["config"])


class ConfigStatus(BaseModel):
    sender_name:     str   # name used in email greetings/signatures
    signature_links: str   # one line of links under the name (GitHub/LinkedIn/…)
    gmail_address:   str = ""     # connected sending address ('' = not connected)
    has_gmail:       bool = False  # true when a connection is stored server-side
    # 'oauth' (one-click grant) | 'app_password' | '' — the frontend uses this
    # to show the right connected-state copy and the right disconnect action.
    gmail_method:    str = ""
    # Whether the one-click OAuth path is available on this deployment.
    oauth_available: bool = False


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
    except smtplib.SMTPAuthenticationError as e:
        # 400, NOT 401: the frontend treats 401 as "session expired" and force
        # logs the user out — a typo'd App Password must not end their session.
        # The message distinguishes a wrong App Password from Gmail blocking the
        # server's IP — very different fixes for the user.
        log.warning(f"Gmail auth rejected for user {user.id}: {e}")
        raise HTTPException(400, mailer.auth_error_message(e))
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


# ── One-click OAuth connect ───────────────────────────────────────────────────

@router.get("/config/gmail/oauth/start")
def gmail_oauth_start(user: User = Depends(get_current_user)):
    """Consent URL for the one-click Gmail connection. The frontend redirects
    the browser there; Google sends it back to the callback below."""
    if not gmail_oauth.enabled():
        raise HTTPException(503, "Gmail OAuth isn't configured on this deployment.")
    return {"url": gmail_oauth.auth_url(gmail_oauth.make_state(user.id))}


class OAuthCompleteRequest(BaseModel):
    code:  str
    state: str


@router.post("/config/gmail/oauth/complete", response_model=ConfigStatus)
def gmail_oauth_complete(
    req: OAuthCompleteRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Authenticated completion of the consent flow. Google redirects the
    browser to the SPA root with ?code&state; the logged-in frontend POSTs
    them here. Requiring BOTH a valid session and state.uid == that session's
    user means a grant can only land on the account that initiated the flow
    AND performed the consent — the old unauthenticated callback trusted the
    state's uid alone, so a crafted consent link could plant/harvest a grant
    across accounts."""
    uid = gmail_oauth.verify_state(req.state)
    if uid is None:
        raise HTTPException(400, "This connection link expired — try Connect Gmail again.")
    if uid != user.id:
        log.warning(f"[user {user.id}] OAuth complete with state for user {uid} — rejected")
        raise HTTPException(400, "This connection attempt belongs to a different session — try again.")
    if not req.code:
        raise HTTPException(400, "Google didn't return a code — try Connect Gmail again.")
    try:
        refresh_token, email = gmail_oauth.exchange_code(req.code)
    except Exception as e:
        log.warning(f"[user {user.id}] Gmail OAuth completion failed: {e}")
        raise HTTPException(502, "Couldn't finish connecting to Google. Please try again.")

    cfg = ConfigRepository(db, user.id)
    cfg.set("gmail_oauth_refresh_token", refresh_token)
    cfg.set("gmail_oauth_address", email)
    log.info(f"[user {user.id}] Gmail connected via OAuth ({email})")
    return _status(db, user)


@router.delete("/config/gmail/oauth", response_model=ConfigStatus)
def gmail_oauth_disconnect(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Disconnect the OAuth grant (revokes it at Google, best effort)."""
    cfg = ConfigRepository(db, user.id)
    _, refresh_token = cfg.get_gmail_oauth()
    if refresh_token:
        gmail_oauth.revoke(refresh_token)
    cfg.set("gmail_oauth_refresh_token", "")
    cfg.set("gmail_oauth_address", "")
    log.info(f"[user {user.id}] Gmail OAuth disconnected")
    return _status(db, user)


def _status(db: Session, user: User) -> ConfigStatus:
    # Signature values are RESOLVED (explicit override → résumé auto-detection),
    # so the frontend can render the preview without its own fallbacks.
    # Batched: ONE config SELECT for all six keys and (only when a signature
    # field needs résumé fallback) ONE résumé read — this endpoint fires on
    # every app load and used to make ~8 serial round trips.
    cfg = ConfigRepository(db, user.id)
    vals = cfg.get_many([
        "gmail_address", "gmail_app_password",
        "gmail_oauth_address", "gmail_oauth_refresh_token",
        "sender_name", "signature_links",
    ])
    addr, pw = vals["gmail_address"], vals["gmail_app_password"]
    oauth_addr, oauth_token = vals["gmail_oauth_address"], vals["gmail_oauth_refresh_token"]
    # OAuth wins when both are present — it's the connection send/inbox prefer.
    if oauth_addr and oauth_token:
        method, shown = "oauth", oauth_addr
    elif addr and pw:
        method, shown = "app_password", addr
    else:
        method, shown = "", addr

    resume_text = ""
    if not vals["sender_name"].strip() or not vals["signature_links"].strip():
        latest = ResumeRepository(db, user.id).get_latest()
        resume_text = latest.text if latest else ""

    return ConfigStatus(
        sender_name=resolve_sender_name(db, user.id, user.email,
                                        cfg_values=vals, resume_text=resume_text),
        signature_links=resolve_signature_links(db, user.id,
                                                cfg_values=vals, resume_text=resume_text),
        gmail_address=shown,
        has_gmail=bool(method),
        gmail_method=method,
        oauth_available=gmail_oauth.enabled(),
    )
