"""
ATS job-board scrapers — Greenhouse, Lever, Ashby.

These public JSON APIs expose active job postings without authentication, so
companies are provably hiring right now. Job descriptions sometimes embed a
recruiter email; when they don't, we emit an identity-only lead carrying the
company domain for the resolver to work on.

All the shared logic lives in BaseATSScraper. Adding a new ATS (Workable,
Recruitee, SmartRecruiters, …) means implementing one method: `_fetch(slug)`.

Query handling (see directory.looks_like_company):
  - company query ("Amazon")        → derive slug(s), hit boards directly
  - role query    ("golang hiring") → scan the directory, keep jobs whose title
                                       matches the role keywords
"""

import asyncio
import logging
import random
import re
from abc import abstractmethod
from urllib.parse import urlparse

import httpx

from app.scrapers.base import BaseScraper, person_name_from_email
from app.scrapers.directory import (
    companies_for_ats, lookup, slugify_company, looks_like_company, role_match,
)

log = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_SKIP_DOMAINS = {
    "greenhouse.io", "lever.co", "ashbyhq.com", "workday.com",
    "linkedin.com", "indeed.com", "example.com", "sentry.io", "sentry-next.wixpress.com",
}
UA = "ColdReach/1.0 (job-board reader)"


def _extract_emails(text: str) -> list[str]:
    return [e for e in _EMAIL_RE.findall(text)
            if e.split("@")[1].lower() not in _SKIP_DOMAINS]


# Suffixes that appear in ATS board slugs but not in company domains.
_SLUG_SUFFIX_RE = re.compile(
    r'[-_]?(inc|hq|labs|tech|technologies|corp|llc|ltd|co|ai|io|app|us|global|group)$',
    re.IGNORECASE,
)


def _slug_to_domain(slug: str) -> str:
    """Best-effort: strip common ATS-slug suffixes then derive a .com domain.

    e.g. "twilio-inc" → "twilio.com", "acme-labs" → "acme.com".
    Imperfect but better than naively gluing the slug together.
    """
    cleaned = _SLUG_SUFFIX_RE.sub("", slug.lower().strip("-_"))
    return cleaned.replace("-", "").replace("_", "") + ".com"


def _domain_from_url(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower().removeprefix("www.")
        parts = host.split(".")
        if len(parts) >= 2:
            return host
    except Exception:
        pass
    return ""


def _strip_html(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


def _job_context(title: str, company: str, location: str, snippet: str,
                  all_titles: list[str] | None = None) -> str:
    """Build a rich context string from ATS job data for the email generator."""
    ctx = f"{company} is actively hiring for '{title}'"
    if location:
        ctx += f" ({location})"
    # List other open roles to show the company is actively scaling
    if all_titles and len(all_titles) > 1:
        others = [t for t in all_titles[1:4] if t != title]
        if others:
            ctx += f". Also hiring: {', '.join(others)}"
    if snippet:
        # Give the LLM enough description to write a role-specific email
        ctx += f".\n\nRole description:\n{snippet[:500]}"
    return ctx


class BaseATSScraper(BaseScraper):
    """Shared discovery logic; subclasses only implement the per-ATS fetch."""

    ats_key: str = ""        # greenhouse | lever | ashby
    MAX_TARGETS = 8          # cap companies probed per hunt (latency budget)

    @abstractmethod
    async def _fetch(self, slug: str) -> tuple[str, str, list[dict]]:
        """
        Fetch a board. Returns (company_name, api_domain, jobs) where each job is
        {"title": str, "location": str, "text": str}. api_domain may be "".
        """
        ...

    async def search(self, query: str, **_) -> list[dict]:
        company_mode = looks_like_company(query)
        targets = self._targets(query, company_mode)[: self.MAX_TARGETS]
        tasks = [self._collect(slug, dh, query, company_mode) for slug, dh in targets]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        leads: list[dict] = []
        for r in results:
            if isinstance(r, list):
                leads.extend(r)
        return leads

    def _targets(self, query: str, company_mode: bool) -> list[tuple[str, str]]:
        """Return (slug, domain_hint) pairs to probe on this ATS."""
        if not company_mode:
            # Role query → scan directory companies, randomized so repeated hunts
            # reach different companies rather than always the same first MAX_TARGETS.
            all_cos = [(c.slug, c.domain) for c in companies_for_ats(self.ats_key)]
            random.shuffle(all_cos)
            return all_cos

        known = lookup(query)
        if known:
            # Known company: only probe the ATS that actually hosts it.
            return [(known.slug, known.domain)] if known.ats == self.ats_key else []
        # Unknown company: try derived slugs (only the right ATS will 200).
        return [(s, "") for s in slugify_company(query)]

    async def _collect(self, slug: str, domain_hint: str, query: str, company_mode: bool) -> list[dict]:
        try:
            company, api_domain, jobs = await self._fetch(slug)
        except Exception:
            return []
        if not jobs:
            return []

        domain = domain_hint or api_domain or _slug_to_domain(slug)

        if not company_mode:
            # Tech-aware filter: "react engineer" must match React roles, not every
            # job titled "…Engineer". Tags aren't available here, so match on title.
            jobs = [j for j in jobs if role_match(query, j["title"])]
            if not jobs:
                return []

        return self._emit(company, domain, jobs, slug)

    def _emit(self, company: str, domain: str, jobs: list[dict], slug: str) -> list[dict]:
        source = f"{self.name}/{slug}"
        embedded: set[str] = set()
        all_titles = [j["title"] for j in jobs if j.get("title")]

        # Build context from the first (most relevant) job with the richest description
        best_job = max(jobs[:5], key=lambda j: len(j.get("text", "")), default=jobs[0] if jobs else {})
        top_ctx = _job_context(
            best_job.get("title", ""),
            company,
            best_job.get("location", ""),
            best_job.get("text", ""),
            all_titles,
        )

        for j in jobs[:5]:
            for em in _extract_emails(j["text"]):
                embedded.add(em)

        leads: list[dict] = []
        for em in list(embedded)[:3]:
            leads.append({
                # Person-like locals only ("sarah.chen" → "Sarah Chen"); role
                # mailboxes get "" so the greeting falls back to "Hi," not "Hi Jobs,".
                "name":        person_name_from_email(em, company),
                "email":       em,
                "company":     company,
                "designation": "Recruiter",
                "source":      source,
                "context":     top_ctx,
            })

        if not leads:
            leads.append({
                "name":        "",
                "email":       "",
                "company":     company,
                "designation": "Recruiter",
                "source":      source,
                "context":     top_ctx,
                "_domain":     domain,
            })
        return leads


# ── Greenhouse ────────────────────────────────────────────────────────────────

class GreenhouseScraper(BaseATSScraper):
    name = "Greenhouse"
    ats_key = "greenhouse"

    async def _fetch(self, slug: str) -> tuple[str, str, list[dict]]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": UA}) as client:
            resp = await client.get(url, params={"content": "true"})
            if not resp.is_success:
                return "", "", []
            data = resp.json()

        jobs_raw = data.get("jobs", [])
        if not jobs_raw:
            return "", "", []
        company = (jobs_raw[0].get("company") or {}).get("name") or slug.title()
        jobs = [{
            "title":    j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "text":     _strip_html(j.get("content", "")),
        } for j in jobs_raw[:10]]
        return company, "", jobs


# ── Lever ─────────────────────────────────────────────────────────────────────

class LeverScraper(BaseATSScraper):
    name = "Lever"
    ats_key = "lever"

    async def _fetch(self, slug: str) -> tuple[str, str, list[dict]]:
        url = f"https://api.lever.co/v0/postings/{slug}"
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": UA}) as client:
            resp = await client.get(url, params={"mode": "json", "limit": "10"})
            if not resp.is_success:
                return "", "", []
            raw = resp.json()
        if not isinstance(raw, list) or not raw:
            return "", "", []

        company = raw[0].get("company") or slug.title()
        jobs = [{
            "title":    j.get("text", ""),
            "location": (j.get("categories") or {}).get("location", ""),
            "text":     _strip_html(" ".join(
                            (b.get("content") or "")
                            for b in (j.get("description") or {}).get("body", [])
                        ) or j.get("descriptionPlain", "")),
        } for j in raw[:10]]
        return company, "", jobs


# ── Ashby ─────────────────────────────────────────────────────────────────────

class AshbyScraper(BaseATSScraper):
    name = "Ashby"
    ats_key = "ashby"

    async def _fetch(self, slug: str) -> tuple[str, str, list[dict]]:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": UA}) as client:
            resp = await client.get(url)
            if not resp.is_success:
                return "", "", []
            data = resp.json()

        jobs_raw = data.get("jobPostings") or data.get("jobs") or []
        if not jobs_raw:
            return "", "", []
        org = data.get("organization") or {}
        company = org.get("name") or slug.replace("-", " ").title()
        api_domain = _domain_from_url(org.get("websiteUrl") or "")
        jobs = [{
            "title":    j.get("title") or j.get("name", ""),
            "location": (j.get("location") or {}).get("city", "") if isinstance(j.get("location"), dict) else (j.get("locationName") or ""),
            "text":     _strip_html(j.get("descriptionHtml") or j.get("description") or ""),
        } for j in jobs_raw[:10]]
        return company, api_domain, jobs


# ── SmartRecruiters ─────────────────────────────────────────────────────────────

def _sr_location(loc: dict) -> str:
    if loc.get("remote"):
        return "Remote"
    return ", ".join(p for p in (loc.get("city"), loc.get("country")) if p)


class SmartRecruitersScraper(BaseATSScraper):
    name = "SmartRecruiters"
    ats_key = "smartrecruiters"

    async def _fetch(self, slug: str) -> tuple[str, str, list[dict]]:
        url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": UA}) as client:
            resp = await client.get(url, params={"limit": 100})
            if not resp.is_success:
                return "", "", []
            data = resp.json()

        postings = data.get("content", [])
        if not postings:
            return "", "", []
        company = (postings[0].get("company") or {}).get("name") or slug
        # The list endpoint carries no description; a per-posting detail call would
        # be one request each, so we surface title + location and let the resolver
        # work the company domain (identity-only lead) instead.
        jobs = [{
            "title":    p.get("name", ""),
            "location": _sr_location(p.get("location") or {}),
            "text":     "",
        } for p in postings[:15]]
        return company, "", jobs


# ── Recruitee ───────────────────────────────────────────────────────────────────

class RecruiteeScraper(BaseATSScraper):
    name = "Recruitee"
    ats_key = "recruitee"

    async def _fetch(self, slug: str) -> tuple[str, str, list[dict]]:
        url = f"https://{slug}.recruitee.com/api/offers/"
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": UA}) as client:
            resp = await client.get(url)
            if not resp.is_success:
                return "", "", []
            data = resp.json()

        offers = data.get("offers", [])
        if not offers:
            return "", "", []
        company = offers[0].get("company_name") or slug.replace("-", " ").title()
        jobs = [{
            "title":    o.get("title", ""),
            "location": o.get("location") or o.get("city", ""),
            "text":     _strip_html(f"{o.get('description','')} {o.get('requirements','')}"),
        } for o in offers[:15]]
        return company, "", jobs


# ── Workable ────────────────────────────────────────────────────────────────────

class WorkableScraper(BaseATSScraper):
    """Workable's public widget API — one of the largest startup/SMB ATSs."""

    name = "Workable"
    ats_key = "workable"

    async def _fetch(self, slug: str) -> tuple[str, str, list[dict]]:
        url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": UA}) as client:
            resp = await client.get(url, params={"details": "true"})
            if not resp.is_success:
                return "", "", []
            data = resp.json()

        jobs_raw = data.get("jobs") or []
        if not jobs_raw:
            return "", "", []
        company = data.get("name") or slug.replace("-", " ").title()
        jobs = [{
            "title":    j.get("title", ""),
            "location": ", ".join(p for p in (j.get("city"), j.get("country")) if p)
                        or ("Remote" if j.get("remote") else ""),
            "text":     _strip_html(j.get("description", "")),
        } for j in jobs_raw[:15]]
        return company, "", jobs


# ── BreezyHR ────────────────────────────────────────────────────────────────────

class BreezyScraper(BaseATSScraper):
    """BreezyHR's public positions feed — heavy on small startups.

    Notes: Breezy's WAF 403s non-browser user agents, and unknown subdomains
    return 200 with an HTML marketing page — so require a JSON content type.
    """

    name = "Breezy"
    ats_key = "breezy"
    _UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

    async def _fetch(self, slug: str) -> tuple[str, str, list[dict]]:
        url = f"https://{slug}.breezy.hr/json"
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": self._UA},
                                     follow_redirects=True) as client:
            resp = await client.get(url)
            if not resp.is_success or "json" not in resp.headers.get("content-type", ""):
                return "", "", []
            try:
                data = resp.json()
            except Exception:
                return "", "", []

        if not isinstance(data, list) or not data:
            return "", "", []
        company = slug.replace("-", " ").title()
        jobs = [{
            "title":    p.get("name", ""),
            "location": ((p.get("location") or {}).get("name", "")
                         if isinstance(p.get("location"), dict) else ""),
            "text":     _strip_html(p.get("description", "")),
        } for p in data[:15] if isinstance(p, dict)]
        return company, "", jobs
