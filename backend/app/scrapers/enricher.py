"""Email enrichment — Hunter.io domain search + pattern derivation."""

import httpx
from app.scrapers.base import BaseScraper


class HunterEnricher(BaseScraper):
    name = "Hunter.io"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def search(self, query: str, **_) -> list[dict]:
        """For Hunter, 'query' is treated as a company/domain."""
        from app.scrapers.directory import looks_like_company
        leads = await self.search_domain(query)
        # Company-name hunts get one EXTRA quota credit spent on the people a
        # job seeker most wants: founders/execs + HR/TA. department=executive,hr
        # in a single call (documented enum values only; no seniority filter —
        # combining seniority=executive with hr would AND together and exclude
        # non-executive recruiters). Role-query hunts skip it: Hunter treats
        # the query as a company name, so the credit would be wasted.
        if looks_like_company(query):
            seen = {l["email"] for l in leads}
            extra = await self.search_domain(query, department="executive,hr")
            leads += [l for l in extra if l["email"] not in seen]
        return leads

    async def search_domain(self, domain_or_company: str, department: str = "") -> list[dict]:
        """Fetch known emails at a domain from Hunter.io. `department` narrows
        to Hunter's documented department enums (e.g. "executive,hr")."""
        params = {
            "company": domain_or_company,
            "api_key": self.api_key,
            "limit": 10,
            "type": "personal",
        }
        if department:
            params["department"] = department
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.hunter.io/v2/domain-search",
                params=params,
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

        # Mirror the published-address paths' two-tier prefix allowlist so we
        # never return support@/sales@/noreply@ and label it a hiring inbox:
        # a dedicated hiring prefix wins; else an acceptable general inbox;
        # else None (Hunter had only non-outreach generics).
        from app.scrapers.web import HIRING_PREFIXES, GENERAL_PREFIXES
        general: str | None = None
        for e in data.get("emails", []):
            value = e.get("value")
            if not (value and value.lower().endswith(f"@{domain.lower()}")):
                continue
            value = value.lower()
            local = value.split("@", 1)[0]
            if local in HIRING_PREFIXES:
                return value
            if general is None and local in GENERAL_PREFIXES:
                general = value
        return general


