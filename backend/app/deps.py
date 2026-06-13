"""Shared FastAPI dependencies — chiefly authentication."""

from fastapi import Depends, HTTPException, Header
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.crud import UserRepository
from app.db.models import User
from app import security


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the bearer token to a User, or 401."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Not authenticated")
    token = authorization.split(" ", 1)[1].strip()
    payload = security.verify_token(token)
    if payload is None:
        raise HTTPException(401, "Invalid or expired session")
    user = UserRepository(db).get_by_id(payload.get("uid"))
    if not user:
        raise HTTPException(401, "User no longer exists")
    # Reject tokens issued before the last logout / password change.
    if payload.get("ver", 0) != user.token_version:
        raise HTTPException(401, "Session has been revoked")
    return user
