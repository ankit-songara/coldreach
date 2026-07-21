"""
YC startup directory scraper — the keyless yc-oss mirror of Y Combinator's
public company index (static JSON on GitHub Pages, refreshed daily).

hiring.json lists ~1,500 companies flagged as actively hiring, each with its
REAL website domain (no guessing), name, batch, industries, and tags — exactly
the founder-heavy startup population the app targets. Leads emitted here are
identity-only domain leads tagged `_pool`: they fill ONLY the resolve/careers
slots left over after organically-discovered leads (which are backed by live
role-matched listings and therefore strictly more relevant).

Licence note: the mirror carries no LICENSE and unofficially mirrors YC's own
site, so nothing beyond the factual projection (name, domain, batch, tagline,
founder names) is persisted — long descriptions are never stored.

For the handful of companies actually emitted, the YC company page exposes a
founders array (full_name + title) in its embedded page props — appended to
the lead's CONTEXT so drafts can address the founder by name. No email is ever
derived from a founder name here (that would violate never-invent); reachable
addresses still come from the normal grounding scan of the company's domain.
"""

import asyncio
import json
import re
import time

import httpx

from app.scrapers.base import BaseScraper
from app.scrapers.directory import looks_like_company, role_match, company_matches
from app.scrapers.jobboards import _domain_from_url

_HIRING_JSON = "https://yc-oss.github.io/api/companies/hiring.json"
_COMPANY_PAGE = "https://www.ycombinator.com/companies/{slug}"
UA = "Mozilla/5.0 (compatible; ColdReach/1.0)"

# The 2.6MB feed is fetched at most once per process per day (0.7s from the
# GitHub Pages CDN, measured) — never per hunt.
_TTL = 86_400
_cache: dict = {"at": float("-inf"), "companies": []}

# Founder entries inside the company page's embedded JSON props.
_FOUNDER_RE = re.compile(
    r'"full_name"\s*:\s*"([^"]{2,60})"[^}]{0,400}?"title"\s*:\s*"([^"]{0,60})"'
)

_MAX_LEADS_PER_HUNT = 12      # pool leads only fill leftover funnel slots
_MAX_FOUNDER_PAGES = 6        # founder-name page fetches per hunt (sem 3)


async def _load_companies() -> list[dict]:
    now = time.monotonic()
    if now - _cache["at"] < _TTL and _cache["companies"]:
        return _cache["companies"]
    try:
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}) as client:
            r = await client.get(_HIRING_JSON)
            if not r.is_success:
                return _cache["companies"]
            companies = [
                c for c in r.json()
                if isinstance(c, dict) and c.get("status") == "Active"
                and c.get("isHiring") and c.get("website") and c.get("name")
            ]
    except Exception:
        return _cache["companies"]
    if companies:
        _cache.update(at=now, companies=companies)
    return _cache["companies"]


async def _founder_names(client: httpx.AsyncClient, slug: str) -> str:
    """"Jane Doe (CEO), John Roe (CTO)" from the YC company page, or ""."""
    try:
        r = await client.get(_COMPANY_PAGE.format(slug=slug))
        if not r.is_success:
            return ""
        pairs = _FOUNDER_RE.findall(r.text)[:4]
        return ", ".join(
            f"{name} ({title})" if title else name
            for name, title in pairs
        )
    except Exception:
        return ""


class YCStartupsScraper(BaseScraper):
    name = "YCStartups"

    async def search(self, query: str, **_) -> list[dict]:
        companies = await _load_companies()
        if not companies:
            return []
        company_mode = looks_like_company(query)

        matched: list[dict] = []
        for c in companies:
            if company_mode:
                if not company_matches(query, c["name"]):
                    continue
            else:
                hay = " ".join(filter(None, [
                    c.get("one_liner") or "",
                    " ".join(c.get("industries") or []),
                    " ".join(c.get("tags") or []),
                ]))
                if not role_match(query, hay):
                    continue
            domain = _domain_from_url(c["website"])
            if not domain:
                continue
            matched.append({**c, "_lead_domain": domain})
            if len(matched) >= _MAX_LEADS_PER_HUNT:
                break

        if not matched:
            return []

        # Founder names for the first few matches — appended to context only.
        founders_by_slug: dict[str, str] = {}
        try:
            sem = asyncio.Semaphore(3)

            async def fetch(slug: str) -> None:
                async with sem:
                    founders_by_slug[slug] = await _founder_names(client, slug)

            async with httpx.AsyncClient(
                timeout=8, headers={"User-Agent": UA}, follow_redirects=True,
            ) as client:
                targets = [c["slug"] for c in matched[:_MAX_FOUNDER_PAGES] if c.get("slug")]
                await asyncio.wait(
                    [asyncio.create_task(fetch(s)) for s in targets],
                    timeout=6,
                ) if targets else None
        except Exception:
            pass

        leads: list[dict] = []
        for c in matched:
            batch = c.get("batch") or "YC"
            # Factual context only — never claim the company is hiring for a
            # specific role (the feed carries no per-role data).
            ctx = (f"YC {batch} startup ({(c.get('one_liner') or '').strip()[:140]}) — "
                   f"listed as actively hiring on the YC directory")
            founders = founders_by_slug.get(c.get("slug") or "")
            if founders:
                ctx += f". Founders: {founders}"
            leads.append({
                "name":        "",
                "email":       "",
                "company":     c["name"],
                "designation": "Recruiter",
                "source":      self.name,
                "context":     ctx,
                "_domain":     c["_lead_domain"],
                "_pool":       True,
            })
        return leads
