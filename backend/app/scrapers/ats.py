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
    company_tags, _TECH_TOKENS,
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
# Require an explicit -/_ separator before the suffix: with [-_]? the alternation
# ate bare word endings too (twilio→twil, openai→open, cisco→cis, studio→stud),
# deriving the wrong company domain. [-_] strips only detachable ATS-slug
# suffixes ("twilio-inc"→"twilio", "acme-labs"→"acme").
_SLUG_SUFFIX_RE = re.compile(
    r'[-_](inc|hq|labs|tech|technologies|corp|llc|ltd|co|ai|io|app|us|global|group)$',
    re.IGNORECASE,
)


def _board_tech_tags(titles) -> list[str]:
    """Tech tokens visible in a board's job titles — learned free from probes
    the hunt already makes, then used to rank future probe targets by query
    relevance. Bare "go" is excluded (it matches "Go To Market"); a golang tag
    is added only on an explicit Go-engineering title or the word "golang"."""
    toks: set[str] = set()
    golang_hint = False
    for t in titles:
        low = (t or "").lower()
        toks |= {w for w in re.split(r"[^a-z0-9+#]+", low) if w}
        if re.search(r"\bgo\b[^,|/]{0,20}\b(?:engineer|developer)\b", low):
            golang_hint = True
    tags = toks & (_TECH_TOKENS - {"go"})
    if golang_hint or "golang" in toks:
        tags.add("golang")
    return sorted(tags)[:20]


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
    # Companies probed per hunt. Profiled: at 4-wide concurrency and ~0.7s per
    # board fetch, 28 targets clear in ~5s — well inside the board budget the
    # old cap of 12 was leaving unused (the budget wall binds, not the count).
    MAX_TARGETS = 28
    # Per-scraper wall: boards that responded by then are kept, stragglers are
    # cancelled — one slow board must not sink the whole source's results.
    BOARD_BUDGET_SECONDS = 12

    @abstractmethod
    async def _fetch(self, client: httpx.AsyncClient, slug: str) -> tuple[str, str, list[dict]]:
        """
        Fetch a board. Returns (company_name, api_domain, jobs) where each job is
        {"title": str, "location": str, "text": str}. api_domain may be "".
        """
        ...

    async def search(self, query: str, *, explored_slugs: frozenset = frozenset(),
                     probed_out: list | None = None, query_variants: tuple = (),
                     query_tokens: frozenset = frozenset(), **_) -> list[dict]:
        """kwargs contract (threaded from hunt.py via safe_search, ignored by
        scrapers that don't declare them):
          explored_slugs — "ats:slug" keys this user's repeat hunts already
            probed for this query; excluded from the shuffle so each re-run
            covers a fresh directory slice (per-ATS wraparound when exhausted).
          probed_out — mutable list; (ats_key, slug, n_leads, board_tags)
            appended for every board whose fetch COMPLETED (cancelled/errored
            fetches are not definitive and must be retried by future hunts).
          query_variants — sibling tech tokens; jobs matching only a variant
            still emit leads, tagged _sibling for downstream deprioritisation.
          query_tokens — the query's tech tokens (incl. aliases + siblings);
            probe targets whose learned tags match rank first.
        """
        company_mode = looks_like_company(query)
        targets = self._targets(query, company_mode, explored_slugs,
                                 query_tokens)[: self.MAX_TARGETS]
        if not targets:
            return []
        # ONE client per source: all this ATS's boards live on one API host, so
        # connection reuse turns N TLS handshakes into 1 — without this, a
        # hunt's ~80 parallel one-shot clients collapsed on slow uplinks.
        sem = asyncio.Semaphore(4)

        async def bounded(slug: str, dh: str) -> list[dict]:
            async with sem:
                return await self._collect(client, slug, dh, query, company_mode,
                                            probed_out, query_variants)

        async with httpx.AsyncClient(
            timeout=10, headers={"User-Agent": UA},
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
        ) as client:
            tasks = [asyncio.create_task(bounded(slug, dh)) for slug, dh in targets]
            done, pending = await asyncio.wait(tasks, timeout=self.BOARD_BUDGET_SECONDS)
        if pending:
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        leads: list[dict] = []
        for t in done:
            if t.cancelled() or t.exception() is not None:
                continue
            r = t.result()
            if isinstance(r, list):
                leads.extend(r)
        return leads

    def _targets(self, query: str, company_mode: bool,
                 explored_slugs: frozenset = frozenset(),
                 query_tokens: frozenset = frozenset()) -> list[tuple[str, str]]:
        """Return (slug, domain_hint) pairs to probe on this ATS."""
        if not company_mode:
            # Role query → scan directory companies the cursor hasn't covered
            # yet, randomized. Wraparound is PER-ATS: four of the seven pools
            # are smaller than MAX_TARGETS and exhaust on the first hunt — when
            # everything is explored, fall back to the full pool rather than
            # returning nothing.
            all_cos = [(c.slug, c.domain) for c in companies_for_ats(self.ats_key)]
            fresh = [t for t in all_cos
                     if f"{self.ats_key}:{t[0].lower()}" not in explored_slugs]
            pool = fresh or all_cos
            random.shuffle(pool)
            if query_tokens:
                # Rank by learned board tags: query-matching companies first,
                # unknown (never probed) next, known-but-off-topic last. The
                # shuffle above keeps rotation fair WITHIN each tier.
                def tier(t: tuple[str, str]) -> int:
                    tags = company_tags(self.ats_key, t[0])
                    if not tags:
                        return 1
                    return 0 if tags & query_tokens else 2
                pool.sort(key=tier)   # stable sort preserves the shuffle per tier
            return pool

        known = lookup(query)
        if known:
            # Known company: only probe the ATS that actually hosts it.
            return [(known.slug, known.domain)] if known.ats == self.ats_key else []
        # Unknown company: try derived slugs (only the right ATS will 200).
        return [(s, "") for s in slugify_company(query)]

    async def _collect(self, client: httpx.AsyncClient, slug: str, domain_hint: str,
                       query: str, company_mode: bool,
                       probed_out: list | None = None,
                       query_variants: tuple = ()) -> list[dict]:
        try:
            company, api_domain, jobs = await self._fetch(client, slug)
        except Exception:
            return []   # not definitive — do NOT mark probed

        # Learned from EVERY completed probe (match or not): what this board
        # hires for, used to rank future probe targets by query relevance.
        board_tags = _board_tech_tags(j.get("title", "") for j in jobs) if jobs else []

        def _done(leads: list[dict]) -> list[dict]:
            # Fetch completed → definitive outcome (even "no jobs"/"no match"),
            # safe for the exploration cursor to record.
            if probed_out is not None:
                probed_out.append((self.ats_key, slug.lower(), len(leads), board_tags))
            return leads

        if not jobs:
            return _done([])

        domain = domain_hint or api_domain
        if not domain:
            # The '<slug>.com' guess is only safe when the slug IS the company
            # name — a mismatched slug can land on an unrelated real company
            # ("solace" → solace.com ≠ Solace Health) and the grounding scan
            # would then persist a real published email attributed to the
            # WRONG company. Discovered rows arrive with empty domains, so
            # this gate is what keeps directory growth quality-neutral.
            guess = _slug_to_domain(slug)
            slug_tokens = {t for t in re.split(r"[^a-z0-9]+", slug.lower()) if t}
            name_tokens = {t for t in re.split(r"[^a-z0-9]+", (company or "").lower()) if t}
            if guess and slug_tokens and slug_tokens <= (name_tokens | {"inc", "hq", "io", "labs", "jobs"}):
                domain = guess

        if not company_mode:
            # Tech-aware filter: "react engineer" must match React roles, not every
            # job titled "…Engineer". Tags aren't available here, so match on title.
            primary = [j for j in jobs if role_match(query, j["title"])]
            if primary:
                jobs = primary
            elif query_variants:
                # Sibling pass over the SAME fetched jobs (zero extra HTTP):
                # per-variant matching, never a concatenated multi-tech query.
                jobs = [j for j in jobs
                        if any(role_match(v, j["title"]) for v in query_variants)]
                if not jobs:
                    return _done([])
                return _done([{**lead, "_sibling": True}
                              for lead in self._emit(company, domain, jobs, slug)])
            else:
                return _done([])

        return _done(self._emit(company, domain, jobs, slug))

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

    async def _fetch(self, client: httpx.AsyncClient, slug: str) -> tuple[str, str, list[dict]]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
        if True:  # shared client passed in by search() — one pool per source
            # No content=true: with descriptions the payload for a big board is
            # multiple MB and forced a 10-job cap — titles-only is small enough
            # to scan the ENTIRE board, which is what makes role matches land.
            resp = await client.get(url)
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
        } for j in jobs_raw[:200]]
        return company, "", jobs


# ── Lever ─────────────────────────────────────────────────────────────────────

class LeverScraper(BaseATSScraper):
    name = "Lever"
    ats_key = "lever"

    async def _fetch(self, client: httpx.AsyncClient, slug: str) -> tuple[str, str, list[dict]]:
        url = f"https://api.lever.co/v0/postings/{slug}"
        if True:  # shared client passed in by search() — one pool per source
            resp = await client.get(url, params={"mode": "json", "limit": "50"})
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
        } for j in raw[:50]]
        return company, "", jobs


# ── Ashby ─────────────────────────────────────────────────────────────────────

class AshbyScraper(BaseATSScraper):
    name = "Ashby"
    ats_key = "ashby"

    async def _fetch(self, client: httpx.AsyncClient, slug: str) -> tuple[str, str, list[dict]]:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        if True:  # shared client passed in by search() — one pool per source
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
        } for j in jobs_raw[:40]]
        return company, api_domain, jobs


# ── SmartRecruiters ─────────────────────────────────────────────────────────────

def _sr_location(loc: dict) -> str:
    if loc.get("remote"):
        return "Remote"
    return ", ".join(p for p in (loc.get("city"), loc.get("country")) if p)


class SmartRecruitersScraper(BaseATSScraper):
    name = "SmartRecruiters"
    ats_key = "smartrecruiters"

    async def _fetch(self, client: httpx.AsyncClient, slug: str) -> tuple[str, str, list[dict]]:
        url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
        if True:  # shared client passed in by search() — one pool per source
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

    async def _fetch(self, client: httpx.AsyncClient, slug: str) -> tuple[str, str, list[dict]]:
        url = f"https://{slug}.recruitee.com/api/offers/"
        if True:  # shared client passed in by search() — one pool per source
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

    async def _fetch(self, client: httpx.AsyncClient, slug: str) -> tuple[str, str, list[dict]]:
        url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
        if True:  # shared client passed in by search() — one pool per source
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

    async def _fetch(self, client: httpx.AsyncClient, slug: str) -> tuple[str, str, list[dict]]:
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
