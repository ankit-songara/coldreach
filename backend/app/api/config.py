"""
Profile / signature / Gmail-connection configuration.

  GET    /api/config          signature + gmail connection status (no secrets)
  POST   /api/config/profile  set the sender name + signature links
  POST   /api/config/gmail    verify and store Gmail creds (password encrypted)
  DELETE /api/config/gmail    forget stored Gmail creds

  GET    /api/config/gmail/oauth/start     one-click connect: consent URL
  GET    /api/config/gmail/oauth/callback  Google redirect target (no auth —
                                           the signed `state` carries the user)
  DELETE /api/config/gmail/oauth           disconnect + best-effort revoke

Secrets (App Password, OAuth refresh token) are Fernet-encrypted with
SECRET_KEY before they touch the database and are never returned by any
endpoint. Send/inbox prefer the OAuth grant when both are present.
"""

import logging
import smtplib
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.config import settings
from app.db.database import get_db
from app.db.crud import ConfigRepository, resolve_sender_name, resolve_signature_links
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


@router.get("/config/gmail/oauth/callback")
def gmail_oauth_callback(
    state: str = "", code: str = "", error: str = "",
    db: Session = Depends(get_db),
):
    """Google's redirect target. Unauthenticated by design — the browser
    arrives without our Authorization header, so the signed short-lived
    `state` token is what attributes (and CSRF-protects) the grant."""
    def back(result: str) -> RedirectResponse:
        # Land on Setup so the user sees the connection state immediately.
        return RedirectResponse(f"{settings.frontend_url.rstrip('/')}/?gmail={result}#setup")

    uid = gmail_oauth.verify_state(state)
    if uid is None:
        return back("error")
    if error or not code:
        # User hit "Cancel" on the consent screen (or Google errored).
        return back("cancelled")
    try:
        refresh_token, email = gmail_oauth.exchange_code(code)
    except Exception:
        return back("error")

    cfg = ConfigRepository(db, uid)
    cfg.set("gmail_oauth_refresh_token", refresh_token)
    cfg.set("gmail_oauth_address", email)
    log.info(f"[user {uid}] Gmail connected via OAuth ({email})")
    return back("connected")


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
    cfg = ConfigRepository(db, user.id)
    addr, pw = cfg.get_gmail_creds()
    oauth_addr, oauth_token = cfg.get_gmail_oauth()
    # OAuth wins when both are present — it's the connection send/inbox prefer.
    if oauth_addr and oauth_token:
        method, shown = "oauth", oauth_addr
    elif addr and pw:
        method, shown = "app_password", addr
    else:
        method, shown = "", addr
    return ConfigStatus(
        sender_name=resolve_sender_name(db, user.id, user.email),
        signature_links=resolve_signature_links(db, user.id),
        gmail_address=shown,
        has_gmail=bool(method),
        gmail_method=method,
        oauth_available=gmail_oauth.enabled(),
    )
