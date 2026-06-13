"""Email enrichment — Hunter.io domain search + pattern derivation."""

import re
import httpx
from app.scrapers.base import BaseScraper


class HunterEnricher(BaseScraper):
    name = "Hunter.io"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def search(self, query: str, **_) -> list[dict]:
        """For Hunter, 'query' is treated as a company/domain."""
        return await self.search_domain(query)

    async def search_domain(self, domain_or_company: str) -> list[dict]:
        """Fetch all known emails at a domain from Hunter.io."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.hunter.io/v2/domain-search",
                params={
                    "company": domain_or_company,
                    "api_key": self.api_key,
                    "limit": 10,
                    "type": "personal",
                },
            )
            if not resp.is_success:
                return []
            data = resp.json().get("data", {})

        org = data.get("organization") or domain_or_company
        return [
            {
                "name":        f"{e.get('first_name','')} {e.get('last_name','')}".strip() or "Contact",
                "email":       e["value"],
                "designation": e.get("position") or "HR/Recruiter",
                "company":     org,
                "source":      self.name,
            }
            for e in data.get("emails", [])
            if e.get("value") and "@" in e["value"]
        ]


# ── Pattern derivation (no API needed) ───────────────────────────────────────

def detect_email_pattern(emails: list[str], domain: str) -> str | None:
    """
    Given emails at a domain, detect the format used.
    e.g. ankit.rao@razorpay.com  → 'firstname.lastname'
         arao@razorpay.com       → 'f.lastname'
         ankit@razorpay.com      → 'firstname'
    """
    domain_emails = [e for e in emails if e.lower().endswith(f"@{domain}")]
    if not domain_emails:
        return None
    local = domain_emails[0].split("@")[0].lower()
    if "." in local:
        parts = local.split(".")
        return "firstname.lastname" if len(parts[0]) > 1 else "f.lastname"
    return "firstname" if len(local) > 3 else "f.lastname"


def apply_pattern(first: str, last: str, domain: str, pattern: str) -> str | None:
    f = re.sub(r"[^a-z]", "", first.lower())
    l = re.sub(r"[^a-z]", "", last.lower())
    if not f:
        return None
    return {
        "firstname.lastname": f"{f}.{l}@{domain}",
        "firstname":          f"{f}@{domain}",
        "f.lastname":         f"{f[0]}.{l}@{domain}" if l else None,
    }.get(pattern)
