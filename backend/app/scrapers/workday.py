"""
Workday CXS job-board scraper.

Thousands of large enterprises host their careers on Workday, exposed through a
public CXS JSON API that needs no auth and no key:

  POST https://{tenant}.{shard}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
       {"limit": 20, "offset": 0, "searchText": "<query>", "appliedFacets": {}}
  → 200 {total, jobPostings: [{title, externalPath, locationsText, postedOn,
                               bulletFields: [jobReqId]}]}

The payload carries POSTINGS ONLY — never an email — and its externalPath points
back at myworkdayjobs.com, NOT the company's own domain. So a match here can only
be an IDENTITY-ONLY DOMAIN LEAD (exactly like the ATS scrapers emit): the company
identity is the tenant slug, and the real email domain comes from our curated
registry, never from the API. That lead feeds ColdReach's careers-inbox grounding.

Discovery of a company's (shard, site) is the hard part and can't be guessed from
the API — every entry in `workday_tenants.csv` was verified against the live API
(200 + non-empty board) before inclusion.

Query handling (see directory.looks_like_company):
  - role query    ("react engineer") → probe MAX_TARGETS shuffled registry
                    tenants, keep postings whose title role_match'es the query,
                    emit one identity-only lead per company (deduped by domain)
  - company query ("Visa")           → fuzzy-match the registry; if found, list
                    that tenant's jobs and emit one lead. Not in registry → []
                    (blind tenant+shard+site discovery is too costly for the
                    serverless budget, so unknown companies are skipped).
"""

import asyncio
import csv
import logging
import random
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.scrapers.base import BaseScraper
from app.scrapers.ats import _job_context
from app.scrapers.directory import looks_like_company, role_match, _norm

log = logging.getLogger(__name__)

UA = "ColdReach/1.0 (job-board reader)"
# Workday hard-caps the CXS page size at 20 regardless of what `limit` we send.
_PAGE_LIMIT = 20


@dataclass(frozen=True)
class WorkdayTenant:
    company: str
    tenant:  str
    shard:   str
    site:    str
    domain:  str

    @property
    def url(self) -> str:
        return (f"https://{self.tenant}.{self.shard}.myworkdayjobs.com"
                f"/wday/cxs/{self.tenant}/{self.site}/jobs")


_CSV_PATH = Path(__file__).with_name("workday_tenants.csv")


def _load_tenants() -> list[WorkdayTenant]:
    """Load the curated tenant registry from workday_tenants.csv (next to this
    module). Every row was live-probed (200 + jobs) before being committed."""
    out: list[WorkdayTenant] = []
    try:
        with _CSV_PATH.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                company = (row.get("company") or "").strip()
                tenant  = (row.get("tenant") or "").strip()
                shard   = (row.get("shard") or "").strip()
                site    = (row.get("site") or "").strip()
                domain  = (row.get("domain") or "").strip().lower()
                if company and tenant and shard and site and domain:
                    out.append(WorkdayTenant(company, tenant, shard, site, domain))
    except FileNotFoundError:
        log.warning("workday_tenants.csv not found at %s — Workday registry empty", _CSV_PATH)
    return out


_TENANTS: list[WorkdayTenant] = _load_tenants()


class WorkdayScraper(BaseScraper):
    """Reads Workday's public CXS API for the curated big-company tenants."""

    name = "Workday"
    MAX_TARGETS = 10          # cap tenants probed per role hunt (latency budget)
    # Per-scraper wall: tenants that responded by then are kept, stragglers are
    # cancelled — one slow tenant must not sink the whole source's results.
    BOARD_BUDGET_SECONDS = 12

    async def search(self, query: str, **_) -> list[dict]:
        company_mode = looks_like_company(query)
        targets = self._targets(query, company_mode)
        if not targets:
            return []
        # ONE client for the source: every tenant lives on *.myworkdayjobs.com, so
        # connection reuse turns N TLS handshakes into a small pool.
        sem = asyncio.Semaphore(4)

        async def bounded(t: WorkdayTenant) -> dict | None:
            async with sem:
                return await self._collect(client, t, query, company_mode)

        async with httpx.AsyncClient(
            timeout=10, headers={"User-Agent": UA},
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
        ) as client:
            tasks = [asyncio.create_task(bounded(t)) for t in targets]
            done, pending = await asyncio.wait(tasks, timeout=self.BOARD_BUDGET_SECONDS)
        if pending:
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

        # One lead per company, deduped by domain (several registry rows could
        # share a domain, and a role scan hits many tenants at once).
        leads: dict[str, dict] = {}
        for t in done:
            if t.cancelled() or t.exception() is not None:
                continue
            r = t.result()
            if isinstance(r, dict):
                leads.setdefault(r["_domain"], r)
        return list(leads.values())

    def _targets(self, query: str, company_mode: bool) -> list[WorkdayTenant]:
        """Registry tenants to probe for this query."""
        if not company_mode:
            # Role query → scan the registry, randomized so repeated hunts reach
            # different companies rather than always the same first MAX_TARGETS.
            tenants = list(_TENANTS)
            random.shuffle(tenants)
            return tenants[: self.MAX_TARGETS]
        match = self._lookup(query)
        return [match] if match else []

    def _lookup(self, query: str) -> WorkdayTenant | None:
        """Exact company-name/slug match against the registry.

        Exact-only (no loose single-token fallback): a common query word that
        is merely one token of a multi-word tenant would cross-match the WRONG
        company — "Discovery"→Warner Bros Discovery, "One"→Capital One,
        "Union"→Western Union. Losing recall on abbreviations is acceptable;
        emitting a wrong-company lead is not.
        """
        norm = _norm(query)
        if not norm:
            return None
        for t in _TENANTS:
            if norm == t.tenant.lower() or norm == _norm(t.company):
                return t
        return None

    async def _collect(self, client: httpx.AsyncClient, t: WorkdayTenant,
                       query: str, company_mode: bool) -> dict | None:
        # Company mode: list the tenant's jobs (empty searchText) — the query is
        # the company name, not a role keyword, so it must not filter the board.
        # Role mode: let Workday pre-filter server-side with the role query.
        search_text = "" if company_mode else query
        try:
            titles, locations = await self._fetch(client, t, search_text)
        except Exception:
            return None
        if not titles:
            return None

        if not company_mode:
            # Tech-aware title filter, same as the ATS scrapers: "react engineer"
            # must match React roles, not every posting Workday's search returned.
            kept = [(ti, loc) for ti, loc in zip(titles, locations) if role_match(query, ti)]
            if not kept:
                return None
            titles = [ti for ti, _ in kept]
            locations = [loc for _, loc in kept]

        return self._emit(t, titles, locations)

    async def _fetch(self, client: httpx.AsyncClient, t: WorkdayTenant,
                     search_text: str) -> tuple[list[str], list[str]]:
        """POST the CXS jobs endpoint. Returns (titles, locations)."""
        body = {"limit": _PAGE_LIMIT, "offset": 0,
                "searchText": search_text, "appliedFacets": {}}
        resp = await client.post(t.url, json=body, headers={
            "Content-Type": "application/json", "Accept": "application/json"})
        if not resp.is_success:
            return [], []
        try:
            data = resp.json()
        except Exception:
            return [], []      # malformed JSON — treat as an empty board
        postings = data.get("jobPostings") or []
        titles, locations = [], []
        for p in postings:
            if not isinstance(p, dict):
                continue
            title = (p.get("title") or "").strip()
            if title:
                titles.append(title)
                locations.append((p.get("locationsText") or "").strip())
        return titles, locations

    def _emit(self, t: WorkdayTenant, titles: list[str], locations: list[str]) -> dict:
        """Build the identity-only domain lead (no email — Workday never has one)."""
        ctx = _job_context(titles[0], t.company, locations[0] if locations else "", "", titles)
        return {
            "name":        "",
            "email":       "",
            "company":     t.company,
            "designation": "Recruiter",
            "source":      f"{self.name}/{t.tenant}",
            "context":     ctx,
            "_domain":     t.domain,
        }
