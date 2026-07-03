"""
Authentication — register, login, current-user.

  POST /api/auth/register   create an account, returns a session token
  POST /api/auth/login      exchange email+password for a session token
  GET  /api/auth/me         current user (requires Bearer token)

The first account to register inherits any pre-existing (legacy single-user)
data, which the migration backfilled to user_id=1.
"""

import time
import logging
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.database import get_db
from app.db.crud import UserRepository
from app.db.models import User
from app.deps import get_current_user
from app import security
from app.config import settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

# ── Login throttle (in-memory, per-process) ───────────────────────────────────
# Simple sliding-window limiter keyed by client IP. Good enough for a
# single-process deployment; swap for Redis if you scale horizontally.
_LOGIN_WINDOW_SECONDS = 300
_LOGIN_MAX_ATTEMPTS   = 10
_login_attempts: dict[str, list[float]] = defaultdict(list)


def _check_login_rate(ip: str) -> None:
    now = time.monotonic()
    hits = [t for t in _login_attempts.get(ip, []) if now - t < _LOGIN_WINDOW_SECONDS]
    if len(hits) >= _LOGIN_MAX_ATTEMPTS:
        _login_attempts[ip] = hits
        raise HTTPException(429, "Too many login attempts. Try again in a few minutes.")
    hits.append(now)
    _login_attempts[ip] = hits

    # Opportunistic sweep so the map can't grow unbounded across many IPs.
    if len(_login_attempts) > 1024:
        stale = [k for k, v in _login_attempts.items()
                 if not v or now - v[-1] > _LOGIN_WINDOW_SECONDS]
        for k in stale:
            _login_attempts.pop(k, None)


class Credentials(BaseModel):
    email:    str
    password: str


class GoogleCredential(BaseModel):
    credential: str   # the Google ID token (JWT) from the Sign-in button


class AuthResponse(BaseModel):
    token: str
    email: str
    user_id: int


class UserOut(BaseModel):
    id:    int
    email: str


_EMAIL_RE = __import__("re").compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.post("/register", response_model=AuthResponse)
def register(creds: Credentials, db: Session = Depends(get_db)):
    if not _EMAIL_RE.match(creds.email.strip()):
        raise HTTPException(400, "Enter a valid email address.")
    if len(creds.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    repo = UserRepository(db)
    if repo.get_by_email(creds.email):
        raise HTTPException(409, "An account with that email already exists.")
    user = repo.create(creds.email, creds.password)
    log.info(f"Registered user {user.email} (id={user.id})")
    return AuthResponse(
        token=security.create_token(user.id, user.token_version),
        email=user.email, user_id=user.id,
    )


@router.post("/login", response_model=AuthResponse)
def login(creds: Credentials, request: Request, db: Session = Depends(get_db)):
    _check_login_rate(request.client.host if request.client else "unknown")
    user = UserRepository(db).get_by_email(creds.email)
    if not user or not security.verify_password(creds.password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password.")
    return AuthResponse(
        token=security.create_token(user.id, user.token_version),
        email=user.email, user_id=user.id,
    )


@router.post("/google", response_model=AuthResponse)
def google_login(payload: GoogleCredential, db: Session = Depends(get_db)):
    """
    Verify a Google Sign-In ID token, then issue our own session token.

    Flow: the frontend Google button returns a signed ID token (JWT). We verify
    its signature against Google's public keys and that its audience is our client
    ID, then map the account to a ColdReach user:
      1. known google_sub          → log that user in
      2. verified email matches an
         existing password account  → link Google to it, log in
      3. otherwise                  → create a new Google-only account
    """
    client_id = (settings.google_client_id or "").strip()
    if not client_id:
        raise HTTPException(503, "Google sign-in is not configured on this server.")

    # Imported lazily so the app still boots if google-auth isn't installed yet.
    from google.oauth2 import id_token as google_id_token
    from google.auth.transport import requests as google_requests

    try:
        idinfo = google_id_token.verify_oauth2_token(
            payload.credential, google_requests.Request(), client_id
        )
    except ValueError:
        # Bad signature, wrong audience, or expired token.
        raise HTTPException(401, "Invalid or expired Google credential.")

    email = (idinfo.get("email") or "").lower().strip()
    sub   = idinfo.get("sub")
    if not sub or not email:
        raise HTTPException(401, "Google account is missing an email address.")
    if not idinfo.get("email_verified", False):
        raise HTTPException(401, "Your Google email address is not verified.")

    repo = UserRepository(db)
    user = repo.get_by_google_sub(sub)
    if user is None:
        existing = repo.get_by_email(email)
        if existing:
            repo.link_google_sub(existing, sub)
            user = existing
            log.info(f"Linked Google identity to existing account {user.email} (id={user.id})")
        else:
            user = repo.create_google_user(email, sub)
            log.info(f"Registered user {user.email} via Google (id={user.id})")

    return AuthResponse(
        token=security.create_token(user.id, user.token_version),
        email=user.email, user_id=user.id,
    )


@router.post("/logout")
def logout(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Invalidate every session token issued for this user."""
    UserRepository(db).bump_token_version(user.id)
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut(id=user.id, email=user.email)
