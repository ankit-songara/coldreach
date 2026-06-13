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
        """Wraps search() with error handling. Always returns a list."""
        try:
            results = await self.search(query, **kwargs)
            valid = [r for r in results if is_valid_email(r.get("email", ""))]
            log.info(f"[{self.name}] {len(valid)} contacts for '{query}'")
            return valid
        except Exception as exc:
            log.warning(f"[{self.name}] failed: {exc}")
            return []
