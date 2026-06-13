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
    hits = [t for t in _login_attempts[ip] if now - t < _LOGIN_WINDOW_SECONDS]
    if len(hits) >= _LOGIN_MAX_ATTEMPTS:
        raise HTTPException(429, "Too many login attempts. Try again in a few minutes.")
    hits.append(now)
    _login_attempts[ip] = hits


class Credentials(BaseModel):
    email:    str
    password: str


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


@router.post("/logout")
def logout(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Invalidate every session token issued for this user."""
    UserRepository(db).bump_token_version(user.id)
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut(id=user.id, email=user.email)
