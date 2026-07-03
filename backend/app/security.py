"""
At-rest encryption for sensitive values (Gmail App Password).

A single Fernet key is generated once and stored in data/.secret_key (chmod 600
on POSIX). It never leaves the machine. We use it to encrypt the Gmail App
Password before it touches the database, so a leaked DB file alone can't send
mail as the user.

This is local-machine protection, not multi-tenant security — that arrives with
real auth (feature #6).
"""

import os
import stat
import json
import hmac
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)

_KEY_PATH = Path(__file__).resolve().parent.parent / "data" / ".secret_key"


def _load_or_create_key() -> bytes:
    _KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _KEY_PATH.exists():
        return _KEY_PATH.read_bytes()
    key = Fernet.generate_key()
    _KEY_PATH.write_bytes(key)
    try:
        os.chmod(_KEY_PATH, stat.S_IRUSR | stat.S_IWUSR)  # 600 — POSIX only
    except OSError:
        log.warning(
            "Could not restrict permissions on %s — on Windows, ensure only "
            "your Windows account has access to the data/ directory.",
            _KEY_PATH,
        )
    log.info("Generated new encryption key at data/.secret_key")
    return key


_fernet = Fernet(_load_or_create_key())


def encrypt(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet.decrypt(token.encode()).decode()
    except InvalidToken:
        log.warning("Failed to decrypt a stored secret (key rotated?)")
        return ""


# ── Password hashing (PBKDF2-HMAC-SHA256, stdlib) ─────────────────────────────
_PBKDF2_ROUNDS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


# ── Session tokens (Fernet-encrypted, self-expiring) ──────────────────────────
TOKEN_TTL_DAYS = 30


def create_token(user_id: int, token_version: int = 0) -> str:
    payload = {
        "uid": user_id,
        "ver": token_version,
        "exp": (datetime.now(timezone.utc) + timedelta(days=TOKEN_TTL_DAYS)).timestamp(),
    }
    return _fernet.encrypt(json.dumps(payload).encode()).decode()


def verify_token(token: str) -> dict | None:
    """Return the token payload ({uid, ver, exp}) if valid and unexpired, else None."""
    try:
        payload = json.loads(_fernet.decrypt(token.encode()).decode())
    except (InvalidToken, ValueError):
        return None
    if payload.get("exp", 0) < datetime.now(timezone.utc).timestamp():
        return None
    return payload
