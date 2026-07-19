"""Email enrichment — Hunter.io domain search + pattern derivation."""

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

    async def search_generic(self, domain: str) -> str | None:
        """
        Look up a known role/generic inbox (careers@, hr@, jobs@, ...) that
        Hunter has on file for this domain. type=personal (used by
        search_domain) excludes these entirely, so this is a separate call —
        used as a secondary grounding signal for the P0 hiring-inbox lead
        when no published address was found on the company's own pages.
        """
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.hunter.io/v2/domain-search",
                params={
                    "domain": domain,
                    "api_key": self.api_key,
                    "limit": 10,
                    "type": "generic",
                },
            )
            if not resp.is_success:
                return None
            data = resp.json().get("data", {})

        for e in data.get("emails", []):
            value = e.get("value")
            if value and value.lower().endswith(f"@{domain.lower()}"):
                return value.lower()
        return None


