"""
HackerNews 'Ask HN: Who is hiring?' scraper (free Algolia API).

Primary path: pull the current monthly "Who is hiring?" thread (posted by the
`whoishiring` bot) and mine its top-level comments — each is a real hiring post,
almost always with a contact email and a description of the role and stack.
Comments are filtered by the query's role keywords (role mode) or company name
(company mode). Falls back to a broad comment search if the thread isn't found.

Context extraction: structured info (stack, remote, pay, team size, stage) is
parsed from every post so the email generator has real facts to anchor the email
on instead of generic filler.

This is the single highest-signal free source: the poster explicitly invited
cold email, so reply rates are far above a guessed address.
"""

import re
import html
from datetime import datetime, timedelta

import httpx

from app.scrapers.base import BaseScraper, person_name_from_email
from app.scrapers.directory import looks_like_company, role_match, company_matches

EMAIL_RE   = re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,7}\b')
COMPANY_RE = re.compile(r'^([A-Z][A-Za-z0-9\s&,.\-]{2,50}?)\s*[|│\-–—]')
_ALGOLIA   = "https://hn.algolia.com/api/v1"
UA         = "ColdReach/1.0 (contact finder)"

# Common tech keywords for stack extraction
_TECH_KEYWORDS = {
    "python", "golang", "go", "rust", "typescript", "javascript", "react", "node",
    "java", "scala", "kotlin", "swift", "ruby", "php", "elixir", "clojure",
    "kubernetes", "k8s", "docker", "aws", "gcp", "azure", "postgres", "postgresql",
    "mysql", "redis", "kafka", "graphql", "grpc", "terraform", "pytorch", "tensorflow",
    "llm", "ml", "ai", "data", "spark", "flink", "dbt", "airflow", "fastapi",
    "django", "rails", "nextjs", "next.js", "vue", "angular", "svelte",
}

_PAY_RE = re.compile(
    # "150k-200k", "150k to 200k", "$150k - $200k", "$150,000 - $200,000"
    r'\$?\s*(\d[\d,]+)\s*[kK]\s*(?:[-–—]+|to)\s*\$?\s*(\d[\d,]+)\s*[kK]'
    r'|\$\s*(\d[\d,]+,\d{3})\s*(?:[-–—]+|to)\s*\$?\s*(\d[\d,]+,\d{3})',
    re.IGNORECASE,
)
_REMOTE_RE  = re.compile(r'\b(remote[-\s]?first|fully remote|remote ok|remote friendly|work from home|wfh|remote)\b', re.IGNORECASE)
_HYBRID_RE  = re.compile(r'\b(hybrid|partially remote|flex(?:ible)? work)\b', re.IGNORECASE)
_ONSITE_RE  = re.compile(r'\b(on[-\s]?site only|in[-\s]?office|no remote|office[-\s]?based)\b', re.IGNORECASE)
_SIZE_RE    = re.compile(r'\b(\d+)[-\s](?:person|people|engineer|employee)s?\b|\bteam of (\d+)\b', re.IGNORECASE)
_STAGE_RE   = re.compile(r'\b(seed|pre[-\s]?seed|series\s*[abcde]|bootstrapped|profitable|ycombinator|y combinator|yc\b)', re.IGNORECASE)
_VISA_RE    = re.compile(r'\b(visa sponsorship|sponsor visa|h[-\s]?1b|tn visa|work authorization)\b', re.IGNORECASE)


class HackerNewsScraper(BaseScraper):
    name = "HackerNews"

    def __init__(self, lookback_days: int = 120):
        self.lookback_days = lookback_days

    async def search(self, query: str, **_) -> list[dict]:
        company_mode = looks_like_company(query)
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}) as client:
            contacts: list[dict] = []
            for story_id in await self._latest_threads(client):
                contacts += await self._thread_contacts(client, story_id, query)
            if not contacts and not company_mode:
                contacts = await self._broad_search(client, query)
        return contacts

    async def _latest_threads(self, client: httpx.AsyncClient) -> list[str]:
        """objectIDs of the 2 most recent 'Who is hiring?' stories."""
        try:
            r = await client.get(f"{_ALGOLIA}/search_by_date", params={
                "tags": "story,author_whoishiring",
                "query": "who is hiring",
                "hitsPerPage": 6,
            })
            if not r.is_success:
                return []
        except Exception:
            return []
        ids = []
        for h in r.json().get("hits", []):
            title = (h.get("title") or "").lower()
            if "who is hiring" in title and "freelancer" not in title and "wants to be hired" not in title:
                ids.append(h.get("objectID"))
        return ids[:2]

    async def _thread_contacts(self, client: httpx.AsyncClient, story_id: str, query: str) -> list[dict]:
        try:
            # "Who is hiring" threads run 400–700 top-level comments; 1000 covers the whole thread.
            r = await client.get(f"{_ALGOLIA}/search", params={
                "tags": f"comment,story_{story_id}",
                "hitsPerPage": 1000,
                "attributesToHighlight": "none",
            })
            if not r.is_success:
                return []
        except Exception:
            return []

        company_mode = looks_like_company(query)
        out: list[dict] = []
        for hit in r.json().get("hits", []):
            text = _clean(hit.get("comment_text", ""))
            if not text:
                continue
            company = _company_of(text)
            if company_mode:
                if not company_matches(query, company):
                    continue
            elif not role_match(query, text):
                continue
            out += _emit(hit, text, self.name, company)
        return out

    async def _broad_search(self, client: httpx.AsyncClient, query: str) -> list[dict]:
        hn_query = query if "hiring" in query.lower() else f"{query} hiring"
        since = int((datetime.now() - timedelta(days=self.lookback_days)).timestamp())
        try:
            r = await client.get(f"{_ALGOLIA}/search", params={
                "query": hn_query,
                "tags": "comment",
                "hitsPerPage": 50,
                "numericFilters": f"created_at_i>{since}",
                "attributesToHighlight": "none",
            })
            if not r.is_success:
                return []
        except Exception:
            return []
        out: list[dict] = []
        for hit in r.json().get("hits", []):
            if not _is_hiring_thread(hit.get("story_title", "")):
                continue
            out += _emit(hit, _clean(hit.get("comment_text", "")), self.name)
        return out


class HNJobsScraper(BaseScraper):
    """
    HN front-page job stories — overwhelmingly YC companies posting
    "Acme (YC W24) Is Hiring a Senior Backend Engineer".

    Distinct from the "Who is hiring?" thread: these are funded startups paying
    for placement, i.e. provably hiring right now. Posts rarely embed an email,
    so most leads are identity-only (company + domain) for the resolver.
    """

    name = "HNJobs"
    MAX  = 8
    LOOKBACK_DAYS = 45

    _YC_RE     = re.compile(r"^(.*?)\s*\(YC\s+[WSF]\d{2}\)", re.IGNORECASE)
    _HIRING_RE = re.compile(r"^(.*?)\s+is\s+(?:hiring|looking)", re.IGNORECASE)

    # Job-post titles rarely name a stack ("Acme (YC W24) Is Hiring"), so a
    # tech-required match would starve this source. For engineering-intent
    # queries, an engineering-titled post at a funded startup is a good lead
    # even when the stack is unstated — the context stays honest about it.
    _ENG_TITLE_RE = re.compile(
        r"\b(engineer|developer|swe|technical|cto|backend|frontend|full[-\s]?stack|"
        r"infra(structure)?|devops|sre|founding)\b|\bis hiring\s*$",
        re.IGNORECASE,
    )
    _ENG_QUERY_RE = re.compile(
        r"\b(engineer|developer|swe|backend|frontend|full[-\s]?stack|devops|sre|"
        r"software|founding|golang|go|python|rust|java|javascript|js|node|react|"
        r"typescript|ts|ruby|php|kubernetes|k8s|ml|ai|data|mobile|ios|android)\b",
        re.IGNORECASE,
    )

    async def search(self, query: str, **_) -> list[dict]:
        from app.scrapers.jobboards import _domain_from_url, _guess_domain

        company_mode = looks_like_company(query)
        since = int((datetime.now() - timedelta(days=self.LOOKBACK_DAYS)).timestamp())
        try:
            async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}) as client:
                r = await client.get(f"{_ALGOLIA}/search_by_date", params={
                    "tags": "job",
                    "hitsPerPage": 200,
                    "numericFilters": f"created_at_i>{since}",
                    "attributesToHighlight": "none",
                })
                if not r.is_success:
                    return []
                hits = r.json().get("hits", [])
        except Exception:
            return []

        leads: list[dict] = []
        seen: set[str] = set()
        for hit in hits:
            title = _clean(hit.get("title", ""))
            text  = _clean(hit.get("story_text", ""))
            if not title:
                continue
            company = self._company_from_title(title)
            if company_mode:
                if not company_matches(query, company):
                    continue
            elif not role_match(query, f"{title} {text}"):
                # Fallback: engineering query + engineering-titled YC post.
                if not (self._ENG_QUERY_RE.search(query) and self._ENG_TITLE_RE.search(title)):
                    continue

            is_yc = bool(self._YC_RE.match(title))
            ctx = f"HN job post{' (YC-funded startup)' if is_yc else ''}: {title}"
            if text:
                ctx += f"\n\nPost excerpt:\n{text[:400]}"

            emails = EMAIL_RE.findall(text)
            if emails:
                key = emails[0]
                if key in seen:
                    continue
                seen.add(key)
                leads.append({
                    "name":        person_name_from_email(emails[0], company),
                    "email":       emails[0],
                    "company":     company,
                    "designation": "Founder / Hiring",
                    "source":      self.name,
                    "context":     ctx,
                })
            else:
                url = hit.get("url") or ""
                if "workatastartup.com" in url or "ycombinator.com" in url:
                    url = ""   # YC application portals, not the company's own site
                domain = _domain_from_url(url) or _guess_domain(company)
                if not domain or domain in seen or company == "Unknown":
                    continue
                seen.add(domain)
                leads.append({
                    "name":        "",
                    "email":       "",
                    "company":     company,
                    "designation": "Founder / Hiring",
                    "source":      self.name,
                    "context":     ctx,
                    "_domain":     domain,
                })
            if len(leads) >= self.MAX:
                break
        return leads

    def _company_from_title(self, title: str) -> str:
        m = self._YC_RE.match(title)
        if m:
            return m.group(1).strip()
        m = self._HIRING_RE.match(title)
        if m:
            return m.group(1).strip()
        return "Unknown"


_HIRING_THREAD_RE = re.compile(r"who is hiring", re.IGNORECASE)
_SEEKER_THREAD_RE = re.compile(
    r"want(?:s)? to be hired|seeking work|freelance|looking for (?:work|a job|opportunities)",
    re.IGNORECASE,
)


def _is_hiring_thread(title: str) -> bool:
    return bool(_HIRING_THREAD_RE.search(title)) and not _SEEKER_THREAD_RE.search(title)


# Fallback for posts without the "Company | ..." header: "Acme is hiring...".
# Only match when the subject is clearly a company (multi-word capitalised noun),
# not a person's name extracted from seeker posts ("Sarah Chen is looking for...").
# We require "is hiring" / "is looking for" / "is seeking" — not bare "is looking"
# which matches too broadly — and reject posts starting with first-person pronouns.
_COMPANY_HIRING_RE = re.compile(
    r"^([A-Z][A-Za-z0-9&.\-]{1,30}(?:\s+[A-Z][A-Za-z0-9&.\-]{1,20})*)\s+"
    r"is\s+(?:hiring|looking for|seeking)\b"
)
_PERSON_NAME_START_RE = re.compile(
    r"^(?:I|We|My|Our|The|This|Hi|Hey|He|She|They)\b", re.IGNORECASE
)


def _company_of(text: str) -> str:
    m = COMPANY_RE.match(text)
    if m:
        return m.group(1).strip()
    # Only attempt the hiring-pattern fallback when the post doesn't open with a
    # pronoun — that eliminates "Sarah Chen is looking for..." seeker posts.
    if not _PERSON_NAME_START_RE.match(text):
        m = _COMPANY_HIRING_RE.match(text)
        if m:
            return m.group(1).strip()
    return "Unknown"


_SEEKER_POST_RE = re.compile(
    r"^(seeking|looking for|available for|open to|want(?:ing)? to|i(?:'m| am) looking|"
    r"i(?:'m| am) a|i(?:'m| am) an|i(?:'m| am) seeking|freelance|hire me|"
    r"resume:|cv:|portfolio:)",
    re.IGNORECASE,
)


def _extract_stack(text: str) -> list[str]:
    """Extract tech stack keywords mentioned in the post."""
    words = set(re.findall(r'[a-zA-Z][a-zA-Z0-9+#.\-]*', text.lower()))
    found = [t for t in _TECH_KEYWORDS if t in words]
    # Also look for capitalised versions like "React", "Python", "Go"
    cap_found = re.findall(
        r'\b(Python|Go|Golang|Rust|TypeScript|JavaScript|React|Node\.?js|'
        r'Kubernetes|K8s|Docker|AWS|GCP|Azure|PostgreSQL|Redis|Kafka|GraphQL|'
        r'FastAPI|Django|Rails|Next\.?js|Vue|Angular|Svelte|PyTorch|TensorFlow)\b',
        text
    )
    combined = list(dict.fromkeys(found + [c.lower() for c in cap_found]))
    return combined[:8]


def _extract_pay(text: str) -> str:
    """Extract salary range from post text, normalised to '$Xk-$Yk' form."""
    m = _PAY_RE.search(text)
    if not m:
        return ""
    nums = [x for x in m.groups() if x is not None]
    if len(nums) >= 2:
        lo_n = int(nums[0].replace(",", ""))
        hi_n = int(nums[1].replace(",", ""))
        if lo_n <= 999:
            lo_n *= 1000
        if hi_n <= 999:
            hi_n *= 1000
        return f"${lo_n // 1000}k-${hi_n // 1000}k"
    if nums:
        n = int(nums[0].replace(",", ""))
        return f"${n if n > 999 else n}k"
    return ""


def _build_hn_context(text: str, company: str) -> str:
    """
    Build a structured context string from an HN 'Who is Hiring' post.
    This gives the email generator real facts to anchor on.
    """
    lines: list[str] = []

    # Remote/hybrid/onsite
    if _REMOTE_RE.search(text):
        lines.append("Remote: yes")
    elif _HYBRID_RE.search(text):
        lines.append("Remote: hybrid")
    elif _ONSITE_RE.search(text):
        lines.append("Remote: no (onsite)")

    # Stack
    stack = _extract_stack(text)
    if stack:
        lines.append(f"Stack: {', '.join(stack)}")

    # Pay
    pay = _extract_pay(text)
    if pay:
        lines.append(f"Compensation: {pay}")

    # Team/company size
    m = _SIZE_RE.search(text)
    if m:
        size = m.group(1) or m.group(2)
        lines.append(f"Team size: ~{size} people")

    # Stage
    m2 = _STAGE_RE.search(text)
    if m2:
        lines.append(f"Stage: {m2.group(1)}")

    # Visa
    if _VISA_RE.search(text):
        lines.append("Visa sponsorship: mentioned")

    # Raw snippet (email-stripped, up to 500 chars)
    snippet = EMAIL_RE.sub("", text).strip()
    if len(snippet) > 500:
        snippet = snippet[:500].rsplit(" ", 1)[0] + "…"

    header = f"From {company}'s 'Who is Hiring' HN post"
    if lines:
        return f"{header}:\n" + "\n".join(f"  {l}" for l in lines) + f"\n\nPost excerpt:\n{snippet}"
    return f"{header}:\n{snippet}"


def _emit(hit: dict, text: str, source: str, company: str | None = None) -> list[dict]:
    if not text:
        return []
    if _SEEKER_POST_RE.match(text.lstrip()):
        return []
    if company is None:
        company = _company_of(text)

    emails = EMAIL_RE.findall(text)
    if not emails:
        return []

    # One HN post = one lead. Use the first email; multiple in one post are usually
    # the same person's aliases, not distinct contacts.
    #
    # Name: derived from the email local part when it looks like a person — NEVER
    # the HN username ('Hi Throwaway123,' torches the reply rate). Empty name →
    # the greeting falls back to a plain 'Hi,'.
    context = _build_hn_context(text, company)
    return [{
        "name":        person_name_from_email(emails[0], company),
        "email":       emails[0],
        "company":     company,
        "designation": "Founder / Hiring",
        "source":      source,
        "context":     context,
    }]


def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(t or ""))).strip()
