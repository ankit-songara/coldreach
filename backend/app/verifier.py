"""
Email verification — catch bad addresses before they bounce.

Three cheap, no-send checks (we deliberately avoid an SMTP RCPT probe, which
hurts sender reputation and is widely blocked):

  1. syntax     — RFC-ish regex
  2. MX records — does the domain actually accept mail?
  3. heuristics — disposable domains (invalid-ish) and role accounts (risky)

Verdict: "valid" | "risky" | "invalid". MX lookups are cached per-process.
"""

import re
import logging
import dns.resolver
import httpx

from app.scrapers.base import ROLE_LOCALS

log = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# Common disposable / throwaway domains — sending here is pointless
_DISPOSABLE = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
    "throwawaymail.com", "yopmail.com", "trashmail.com", "getnada.com",
    "temp-mail.org", "fakeinbox.com", "sharklasers.com", "maildrop.cc",
}

# Role accounts — reach a team, not a person; deliverable but low-value.
# ONE source of truth (scrapers.base.ROLE_LOCALS) plus delivery-side extras
# that base has no reason to know about.
_ROLE_LOCALPARTS = ROLE_LOCALS | {
    "noreply", "no-reply", "donotreply", "webmaster", "postmaster",
    "abuse", "marketing",
}

_mx_cache: dict[str, bool] = {}


def _has_mx(domain: str) -> bool:
    if domain in _mx_cache:
        return _mx_cache[domain]
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        ok = len(answers) > 0
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
        ok = False
    except dns.exception.Timeout:
        # Transient network failure — treat as unknown-ok for this call but do
        # NOT cache it, or a single blip permanently whitelists the domain.
        log.warning(f"MX lookup timed out for {domain}")
        return True
    except Exception as e:
        log.warning(f"MX lookup error for {domain}: {e}")
        return True
    _mx_cache[domain] = ok   # cache only definitive answers
    return ok


def verify_email(email: str) -> str:
    """Return 'valid' | 'risky' | 'invalid'."""
    email = (email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        return "invalid"

    local, _, domain = email.partition("@")

    if domain in _DISPOSABLE:
        return "invalid"

    if not _has_mx(domain):
        return "invalid"

    if local in _ROLE_LOCALPARTS:
        return "risky"

    return "valid"
