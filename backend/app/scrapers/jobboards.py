"""
Free remote-job board scrapers — RemoteOK, Remotive, Arbeitnow.

These public JSON feeds list roles companies are hiring for *right now*, with no
API key. For each matching listing we either:
  - extract an email embedded in the description       → direct lead, or
  - emit an identity-only domain lead (best-effort)    → resolver fills the email.

Role queries ("golang hiring") filter listings by title/tags; company queries
("Stripe") filter by company name. Adding another JSON board = one subclass
implementing `_listings`.
"""

import re
import html
from urllib.parse import urlparse

import httpx

from app.scrapers.base import BaseScraper, person_name_from_email
from app.scrapers.directory import looks_like_company, role_match, company_matches, _norm

EMAIL_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')
UA = "ColdReach/1.0 (job-board reader)"

# Domains that are aggregators / webmail / ATS hosts, not the employer — never
# treat an email or URL at these as a company contact.
_AGG = (
    "remoteok", "remotive", "arbeitnow", "greenhouse.io", "lever.co", "ashbyhq",
    "workable", "linkedin", "indeed", "glassdoor", "gmail.", "googlemail",
    "outlook.", "yahoo.", "hotmail.", "example.com", "sentry.io",
)


def _strip(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(t or ""))).strip()


def _emails(text: str) -> list[str]:
    out = []
    for e in EMAIL_RE.findall(text or ""):
        if not any(a in e.split("@")[1].lower() for a in _AGG):
            out.append(e.lower())
    return out


def _domain_from_url(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower().removeprefix("www.")
        if host and "." in host and not any(a in host for a in _AGG):
            return host
    except Exception:
        pass
    return ""


_LEGAL_SUFFIX = re.compile(r"\b(inc|llc|ltd|corp|co|gmbh|company)\.?$", re.IGNORECASE)
_IS_DOMAIN    = re.compile(r"^[a-z0-9\-]+\.(io|com|ai|co|dev|app|sh|tech|xyz|net|org|so|gg|cloud|hq)$")


def _guess_domain(company: str) -> str:
    c = _LEGAL_SUFFIX.sub("", company.strip()).strip()
    # Company already written as a domain, e.g. "Lemon.io", "Fly.io".
    compact = c.replace(" ", "").lower()
    if _IS_DOMAIN.match(compact):
        return compact
    # Preserve INTERNAL hyphens ("X-Team" → x-team.com) — _norm strips them,
    # making hyphenated-domain companies permanently unreachable. Spaces still
    # collapse ("Acme Widgets" → acmewidgets.com; the legal suffix in "Acme
    # Corp" is already stripped above → acme.com). The guess is only ever
    # accepted downstream if its domain has real MX, so nothing ungrounded is
    # persisted.
    s = re.sub(r"[^a-z0-9-]", "", c.lower())
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return f"{s}.com" if s else ""


def _matches(query: str, company_mode: bool, title: str, tags: list[str], company: str) -> bool:
    if company_mode:
        return company_matches(query, company)
    # Tech-aware: "react engineer" must match React roles, not every "…Engineer".
    return role_match(query, f"{title} {' '.join(tags)}")


class _JsonBoard(BaseScraper):
    """Shared logic: fetch listings, filter, extract emails or emit domain leads."""

    MAX = 10   # leads kept per board per hunt (latency + quality budget)

    async def _listings(self, client: httpx.AsyncClient) -> list[dict]:
        """Return normalized dicts: {title, company, tags[], text, domain}."""
        raise NotImplementedError

    async def search(self, query: str, **_) -> list[dict]:
        company_mode = looks_like_company(query)
        try:
            async with httpx.AsyncClient(
                timeout=20, headers={"User-Agent": UA}, follow_redirects=True,
            ) as client:
                items = await self._listings(client)
        except Exception:
            return []

        leads: list[dict] = []
        seen_emails: set[str] = set()
        seen_domains: set[str] = set()

        for it in items:
            if not it.get("title") or not _matches(
                query, company_mode, it["title"], it.get("tags", []), it.get("company", "")
            ):
                continue

            company = it.get("company") or "Unknown"
            ctx = f"Actively hiring for '{it['title']}' at {company} (via {self.name})"

            embedded = _emails(it.get("text", ""))
            if embedded:
                em = embedded[0]
                if em in seen_emails:
                    continue
                seen_emails.add(em)
                leads.append({
                    # Person-like locals only; role mailboxes get "" so the greeting
                    # falls back to "Hi," instead of "Hi Jobs,".
                    "name":        person_name_from_email(em, company),
                    "email":       em,
                    "company":     company,
                    "designation": "Recruiter",
                    "source":      self.name,
                    "context":     ctx,
                })
            else:
                # No embedded email (the norm for these boards — they route through
                # apply links). Emit an identity-only domain lead so the resolver can
                # scrape the careers page / probe mailboxes. Real apply-URL domain is
                # preferred; otherwise best-effort guess from the company name.
                if company in ("", "Unknown"):
                    continue
                domain = it.get("domain") or _guess_domain(company)
                if not domain or domain in seen_domains:
                    continue
                seen_domains.add(domain)
                leads.append({
                    "name":        "",
                    "email":       "",
                    "company":     company,
                    "designation": "Recruiter",
                    "source":      self.name,
                    "context":     ctx,
                    "_domain":     domain,
                })

            if len(leads) >= self.MAX:
                break
        return leads


class RemoteOKScraper(_JsonBoard):
    name = "RemoteOK"

    async def _listings(self, client: httpx.AsyncClient) -> list[dict]:
        r = await client.get("https://remoteok.com/api")
        data = r.json() if r.is_success else []
        out = []
        for j in data:
            if not isinstance(j, dict) or not j.get("position"):
                continue   # first element is a legal notice, skip non-jobs
            out.append({
                "title":   j.get("position", ""),
                "company": j.get("company", "") or "Unknown",
                "tags":    [str(t) for t in (j.get("tags") or [])],
                "text":    _strip(j.get("description", "")),
                "domain":  _domain_from_url(j.get("apply_url") or ""),
            })
        return out


class RemotiveScraper(_JsonBoard):
    name = "Remotive"

    async def _listings(self, client: httpx.AsyncClient) -> list[dict]:
        r = await client.get("https://remotive.com/api/remote-jobs", params={"limit": 200})
        jobs = (r.json().get("jobs") if r.is_success else []) or []
        return [{
            "title":   j.get("title", ""),
            "company": j.get("company_name", "") or "Unknown",
            "tags":    [str(t) for t in (j.get("tags") or [])],
            "text":    _strip(j.get("description", "")),
            "domain":  "",
        } for j in jobs]


class ArbeitnowScraper(_JsonBoard):
    name = "Arbeitnow"

    async def _listings(self, client: httpx.AsyncClient) -> list[dict]:
        r = await client.get("https://www.arbeitnow.com/api/job-board-api")
        data = (r.json().get("data") if r.is_success else []) or []
        return [{
            "title":   j.get("title", ""),
            "company": j.get("company_name", "") or "Unknown",
            "tags":    [str(t) for t in (j.get("tags") or [])],
            "text":    _strip(j.get("description", "")),
            "domain":  "",
        } for j in data]


class JobicyScraper(_JsonBoard):
    name = "Jobicy"

    async def _listings(self, client: httpx.AsyncClient) -> list[dict]:
        r = await client.get("https://jobicy.com/api/v2/remote-jobs", params={"count": 100})
        jobs = (r.json().get("jobs") if r.is_success else []) or []
        return [{
            "title":   j.get("jobTitle", ""),
            "company": j.get("companyName", "") or "Unknown",
            "tags":    [str(t) for t in (j.get("jobIndustry") or [])]
                       + [str(t) for t in (j.get("jobType") or [])],
            "text":    _strip(j.get("jobExcerpt", "")),
            "domain":  "",
        } for j in jobs]


class HimalayasScraper(_JsonBoard):
    name = "Himalayas"

    async def _listings(self, client: httpx.AsyncClient) -> list[dict]:
        r = await client.get("https://himalayas.app/jobs/api", params={"limit": 100})
        data = r.json() if r.is_success else {}
        jobs = (data.get("jobs") or data.get("data") or []) if isinstance(data, dict) else []
        return [{
            "title":   j.get("title", ""),
            "company": j.get("companyName", "") or "Unknown",
            "tags":    [str(t) for t in (j.get("seniority") or [])],
            "text":    _strip(j.get("excerpt", "")),
            "domain":  "",
        } for j in jobs]


class WorkingNomadsScraper(_JsonBoard):
    name = "WorkingNomads"

    async def _listings(self, client: httpx.AsyncClient) -> list[dict]:
        r = await client.get("https://www.workingnomads.com/api/exposed_jobs/")
        jobs = (r.json() if r.is_success else []) or []
        return [{
            "title":   j.get("title", ""),
            "company": j.get("company_name", "") or "Unknown",
            # tags is a comma-joined string on this board, not a list.
            "tags":    [t.strip() for t in (j.get("tags") or "").split(",") if t.strip()]
                       + ([j["category_name"]] if j.get("category_name") else []),
            "text":    _strip(j.get("description", "")),
            "domain":  "",
        } for j in jobs if isinstance(j, dict)]


class TheMuseScraper(_JsonBoard):
    name = "TheMuse"

    async def _listings(self, client: httpx.AsyncClient) -> list[dict]:
        out: list[dict] = []
        for page in (1, 2):
            r = await client.get("https://www.themuse.com/api/public/jobs", params={"page": page})
            if not r.is_success:
                break
            for j in r.json().get("results", []):
                tags = [(t or {}).get("name", "") for t in (j.get("categories") or [])] \
                     + [(t or {}).get("name", "") for t in (j.get("levels") or [])]
                out.append({
                    "title":   j.get("name", ""),
                    "company": ((j.get("company") or {}).get("name")) or "Unknown",
                    "tags":    [t for t in tags if t],
                    "text":    _strip(j.get("contents", "")),
                    "domain":  "",
                })
        return out


class WeWorkRemotelyScraper(_JsonBoard):
    name = "WeWorkRemotely"

    async def _listings(self, client: httpx.AsyncClient) -> list[dict]:
        r = await client.get("https://weworkremotely.com/remote-jobs.rss")
        if not r.is_success:
            return []
        out: list[dict] = []
        for block in re.findall(r"<item>(.*?)</item>", r.text, re.DOTALL):
            block = block.replace("<![CDATA[", "").replace("]]>", "")
            title_m = re.search(r"<title>(.*?)</title>", block, re.DOTALL)
            desc_m  = re.search(r"<description>(.*?)</description>", block, re.DOTALL)
            raw_title = html.unescape((title_m.group(1) if title_m else "").strip())
            # WWR titles are formatted "Company: Role".
            company, sep, role = raw_title.partition(":")
            if not sep:
                company, role = "Unknown", raw_title
            out.append({
                "title":   role.strip(),
                "company": company.strip() or "Unknown",
                "tags":    [],
                "text":    _strip(desc_m.group(1) if desc_m else ""),
                "domain":  "",
            })
        return out
