"""
Gmail OAuth 2.0 + Gmail REST API plumbing — the one-click "Connect Gmail" path.

Flow (authorization-code):
  1. /config/gmail/oauth/start builds the consent URL (state = short-lived
     purpose-tagged token so the unauthenticated callback can attribute the
     grant to a user without trusting the query string).
  2. Google redirects to /config/gmail/oauth/callback?code=…&state=…
  3. exchange_code() swaps the code for a refresh token (stored Fernet-
     encrypted in app_config) and reads the connected address from the
     Gmail profile endpoint.
  4. Sending uses access_token_for() + send_raw() — plain HTTPS to the Gmail
     API, which is both allowed and more reliable on serverless than SMTP.

Scopes: gmail.send (send only) + gmail.readonly (reply detection). While the
Google OAuth app is in Testing mode, refresh tokens expire after ~7 days —
callers must treat an invalid_grant as "disconnected, please reconnect".
"""

import base64
import json
import logging
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

import httpx

from app.config import settings
from app import security

log = logging.getLogger(__name__)

SCOPES = "https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.readonly"
_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
_GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"

_STATE_TTL_MINUTES = 10


class OAuthNotConfigured(Exception):
    """google_client_secret (or client id) missing — feature is off."""


class GrantRevoked(Exception):
    """Refresh token no longer works (revoked, or expired in Testing mode)."""


def enabled() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def redirect_uri() -> str:
    return f"{settings.backend_public_url.rstrip('/')}/api/config/gmail/oauth/callback"


# ── State token (CSRF + user attribution for the unauthenticated callback) ────

def make_state(user_id: int) -> str:
    payload = {
        "purpose": "gmail-oauth",
        "uid": user_id,
        "exp": (datetime.now(timezone.utc) + timedelta(minutes=_STATE_TTL_MINUTES)).timestamp(),
    }
    return security._fernet.encrypt(json.dumps(payload).encode()).decode()


def verify_state(state: str) -> int | None:
    """user_id if the state is genuine and fresh, else None."""
    try:
        payload = json.loads(security._fernet.decrypt(state.encode()).decode())
    except Exception:
        return None
    if payload.get("purpose") != "gmail-oauth":
        return None
    if payload.get("exp", 0) < datetime.now(timezone.utc).timestamp():
        return None
    uid = payload.get("uid")
    return uid if isinstance(uid, int) else None


# ── OAuth endpoints ───────────────────────────────────────────────────────────

def auth_url(state: str) -> str:
    if not enabled():
        raise OAuthNotConfigured()
    return _AUTH_ENDPOINT + "?" + urlencode({
        "client_id":     settings.google_client_id,
        "redirect_uri":  redirect_uri(),
        "response_type": "code",
        "scope":         SCOPES,
        # offline + consent guarantees a refresh_token on every connect,
        # not just the first one.
        "access_type":   "offline",
        "prompt":        "consent",
        "state":         state,
    })


def exchange_code(code: str) -> tuple[str, str]:
    """(refresh_token, connected_email). Raises on any failure."""
    if not enabled():
        raise OAuthNotConfigured()
    with httpx.Client(timeout=15) as client:
        r = client.post(_TOKEN_ENDPOINT, data={
            "client_id":     settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "code":          code,
            "grant_type":    "authorization_code",
            "redirect_uri":  redirect_uri(),
        })
        if not r.is_success:
            log.warning(f"Gmail OAuth code exchange failed: {r.status_code} {r.text[:200]}")
            raise RuntimeError("code exchange failed")
        tokens = r.json()
        refresh = tokens.get("refresh_token")
        access = tokens.get("access_token")
        if not refresh or not access:
            # No refresh_token happens when prompt=consent was dropped — treat
            # as failure so the user retries rather than half-connecting.
            raise RuntimeError("no refresh token in response")
        profile = client.get(
            f"{_GMAIL_API}/profile",
            headers={"Authorization": f"Bearer {access}"},
        )
        email = profile.json().get("emailAddress", "") if profile.is_success else ""
    return refresh, email


def access_token_for(refresh_token: str) -> str:
    """Fresh access token from the stored refresh token."""
    if not enabled():
        raise OAuthNotConfigured()
    with httpx.Client(timeout=15) as client:
        r = client.post(_TOKEN_ENDPOINT, data={
            "client_id":     settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "refresh_token": refresh_token,
            "grant_type":    "refresh_token",
        })
    if r.status_code == 400 and "invalid_grant" in r.text:
        # Revoked by the user, or the 7-day Testing-mode expiry hit.
        raise GrantRevoked()
    if not r.is_success:
        log.warning(f"Gmail OAuth refresh failed: {r.status_code} {r.text[:200]}")
        raise RuntimeError("token refresh failed")
    return r.json()["access_token"]


def revoke(refresh_token: str) -> None:
    """Best-effort server-side revocation on disconnect."""
    try:
        httpx.post(_REVOKE_ENDPOINT, data={"token": refresh_token}, timeout=10)
    except Exception:
        pass


# ── Gmail API operations ──────────────────────────────────────────────────────

def send_raw(access_token: str, mime_bytes: bytes) -> None:
    """Send one RFC-822 message via the Gmail API. Raises on failure."""
    raw = base64.urlsafe_b64encode(mime_bytes).decode()
    with httpx.Client(timeout=30) as client:
        r = client.post(
            f"{_GMAIL_API}/messages/send",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"raw": raw},
        )
    if r.status_code == 401:
        raise GrantRevoked()
    if not r.is_success:
        detail = ""
        try:
            detail = r.json().get("error", {}).get("message", "")
        except Exception:
            pass
        raise RuntimeError(detail or f"Gmail API send failed ({r.status_code})")


def find_replies_from(access_token: str, sender: str, after_epoch: int) -> list[dict]:
    """Inbox messages from `sender` after the cutoff — for reply detection.

    Returns [{subject, snippet, received_at(datetime)}], oldest first.
    Uses Gmail's own search (q=) so we never page the whole inbox.
    """
    out: list[dict] = []
    with httpx.Client(timeout=20) as client:
        headers = {"Authorization": f"Bearer {access_token}"}
        r = client.get(
            f"{_GMAIL_API}/messages",
            headers=headers,
            params={"q": f"from:{sender} in:inbox after:{after_epoch}", "maxResults": 5},
        )
        if r.status_code == 401:
            raise GrantRevoked()
        if not r.is_success:
            raise RuntimeError(f"Gmail API list failed ({r.status_code})")
        for stub in r.json().get("messages", []) or []:
            m = client.get(
                f"{_GMAIL_API}/messages/{stub['id']}",
                headers=headers,
                params={"format": "metadata", "metadataHeaders": ["Subject", "Date"]},
            )
            if not m.is_success:
                continue
            data = m.json()
            hdrs = {h["name"].lower(): h["value"]
                    for h in data.get("payload", {}).get("headers", [])}
            # internalDate is epoch millis — more reliable than parsing Date.
            try:
                received = datetime.fromtimestamp(int(data.get("internalDate", 0)) / 1000, tz=timezone.utc)
            except (TypeError, ValueError):
                received = datetime.now(timezone.utc)
            out.append({
                "subject": hdrs.get("subject", ""),
                "snippet": (data.get("snippet") or "")[:400],
                "received_at": received.replace(tzinfo=None),   # naive UTC, matching the DB
            })
    out.sort(key=lambda x: x["received_at"])
    return out
