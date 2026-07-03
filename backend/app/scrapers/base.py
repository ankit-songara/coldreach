"""
Base scraper interface — Strategy pattern.

Every scraper implements BaseScraper. The hunt endpoint
composes multiple scrapers and runs them in parallel.

To add a new source:
  1. Create backend/app/scrapers/mysource.py
  2. Implement BaseScraper
  3. Register in app/api/hunt.py's SCRAPERS list
"""

from abc import ABC, abstractmethod
import logging
import re

log = logging.getLogger(__name__)

# Emails that should never appear in results
SKIP_EMAILS: set[str] = {
    "noreply", "no-reply", "donotreply", "mailer-daemon",
    "bounce", "abuse", "spam", "postmaster",
}


def is_valid_email(email: str) -> bool:
    e = email.lower().strip()
    if "@" not in e:
        return False
    local, domain = e.split("@", 1)
    return (
        len(local) > 0                   # local part must exist
        and "." in domain                # domain must have a dot
        and not any(s in e for s in SKIP_EMAILS)
    )


# Role mailboxes — an email local part here is never a person's name.
ROLE_LOCALS = frozenset({
    "jobs", "careers", "career", "hiring", "hr", "talent", "recruiting", "recruit",
    "team", "info", "hello", "contact", "support", "admin", "work", "apply",
    "join", "joinus", "founders", "office", "mail", "people", "hey", "hi", "sales",
    "help", "press", "media", "partnerships", "general",
})


def person_name_from_email(email: str, company: str = "") -> str:
    """
    Derive a display name from an email local part IF it plausibly names a person
    ('sarah.chen@…' → 'Sarah Chen'). Role mailboxes, digit-bearing locals and
    company-named mailboxes yield '' — a greeting of 'Hi,' beats 'Hi Jobs,'.
    """
    local = email.split("@", 1)[0].lower()
    if local in ROLE_LOCALS or any(ch.isdigit() for ch in local):
        return ""
    parts = [p for p in re.split(r"[._\-+]+", local) if p.isalpha()]
    if not parts or len(parts) > 3 or any(p in ROLE_LOCALS for p in parts):
        return ""
    comp_norm = re.sub(r"[^a-z0-9]", "", (company or "").lower())
    if len(parts) == 1:
        tok = parts[0]
        # Single token: only accept short, non-company-like tokens ('sarah@acme.com').
        if len(tok) < 3 or len(tok) > 10:
            return ""
        if comp_norm and (tok in comp_norm or comp_norm in tok):
            return ""   # 'acme@acme.com' — a company mailbox, not a person
        return tok.title()
    return " ".join(p.title() for p in parts)


class BaseScraper(ABC):
    """All scrapers implement this interface."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable source name for logging and attribution."""
        ...

    @abstractmethod
    async def search(self, query: str, **kwargs) -> list[dict]:
        """
        Search for hiring contacts matching the query.

        Returns list of dicts with keys:
          name, email, company, designation, source
        """
        ...

    async def safe_search(self, query: str, **kwargs) -> list[dict]:
        """
        Wraps search() with error handling. Always returns a list.

        Keeps two kinds of result: those with a valid email, and identity-only
        leads carrying a `_domain` hint (resolved downstream in the hunt flow).
        """
        try:
            results = await self.search(query, **kwargs)
            valid = [
                r for r in results
                if is_valid_email(r.get("email", "")) or r.get("_domain")
            ]
            log.info(f"[{self.name}] {len(valid)} leads for '{query}'")
            return valid
        except Exception as exc:
            log.warning(f"[{self.name}] failed: {exc}")
            return []
