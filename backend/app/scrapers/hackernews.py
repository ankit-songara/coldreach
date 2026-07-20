"""
Hacker News "Who is hiring?" scraper (via the public Algolia HN Search API).

Every month a `whoishiring` bot posts an "Ask HN: Who is hiring?" thread whose
top-level comments are individual job posts, by convention formatted
"Company | Role | Location | ... | apply: email-or-url". Research-verified as
the single highest-yield free source for ColdReach: ~1 in 4 posts embeds a
recruiter/founder email directly, and ~85% carry the company's own URL — both
exactly what the careers-inbox grounding needs.

Two unauthenticated GETs, no key:
  1. find the current thread id (author:whoishiring, title "Ask HN: Who is hiring?")
  2. pull its top-level comments (numericFilters parent_id == story_id)

SAFETY: only the *hiring* thread is read — never the sibling "Who wants to be
hired?" / "Freelancer?" threads the same bot posts at the same timestamp — so
posts are employers, not job-seekers. A belt-and-braces seeker-phrase guard
drops any stray "seeking / looking for work" post regardless.
"""

import html
import re
import time

import httpx

from app.scrapers.base import BaseScraper, person_name_from_email
from app.scrapers.directory import looks_like_company, role_match, company_matches

_ALGOLIA = "https://hn.algolia.com/api/v1/search_by_date"
UA = "ColdReach/1.0 (job-board reader)"

EMAIL_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')
_URL_RE  = re.compile(r'https?://([A-Za-z0-9.\-]+)')

# Aggregator / webmail / ATS hosts — an address or URL here is not the employer.
# Matched by registrable-domain boundary (host == a or endswith "."+a), NOT raw
# substring: bare substring meant "x.com" nuked netflix.com, and "ashby" nuked
# any company with "ashby" in the name.
_AGG = frozenset({
    "ycombinator.com", "news.ycombinator.com", "greenhouse.io", "lever.co",
    "ashbyhq.com", "workable.com", "myworkdayjobs.com", "linkedin.com",
    "indeed.com", "glassdoor.com", "gmail.com", "googlemail.com", "outlook.com",
    "yahoo.com", "hotmail.com", "protonmail.com", "proton.me", "example.com",
    "github.com", "twitter.com", "x.com", "notion.so", "docs.google.com",
    "forms.gle", "join.com", "uctalent.io", "wellfound.com", "angel.co",
    "rippling.com", "bamboohr.com", "teamtailor.com", "smartrecruiters.com",
    "recruitee.com", "breezy.hr", "airtable.com", "typeform.com", "tally.so",
    "youtube.com",
})


def _is_agg(host: str) -> bool:
    """True if host IS or is a subdomain of an aggregator domain (boundary match)."""
    host = host.lower().removeprefix("www.")
    return any(host == a or host.endswith("." + a) for a in _AGG)


# A post that is really a job-SEEKER (rare in the hiring thread). Only
# unambiguous first-person seeking phrases — NOT bare "seeking"/"looking for",
# which are standard EMPLOYER phrasing ("we're seeking a Senior Go Engineer")
# and were silently deleting real hiring posts.
_SEEKER_RE = re.compile(
    r"\b(open to work|available for hire|seeking (?:a )?(?:new )?(?:role|position|job|opportunit)|"
    r"looking for (?:a )?(?:new )?(?:role|position|job|work|opportunit)|"
    r"want(?:ing)? to be hired)\b",
    re.IGNORECASE,
)

# ── Per-process thread cache ───────────────────────────────────────────────────
# The thread is identical for every query in a month and changes slowly, so one
# fetch serves every hunt. Keyed nothing (global) with a 1h TTL; the monthly
# thread flip is picked up on the next refresh.
_TTL = 3600
_cache: dict = {"at": 0.0, "posts": []}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(text or ""))).strip()


def _company_from_post(text: str) -> str:
    """HN posts open with 'Company | Role | ...' by convention — take the first
    segment when it looks like a company name, not a sentence fragment."""
    head = re.split(r"[|—:\n]", text, 1)[0].strip()
    # Reject fragments: too long, prose punctuation, or a lowercase opener
    # (real names/brands are capitalised; "Can you change this…" is not one).
    if not (0 < len(head) <= 40):
        return ""
    if "@" in head or "http" in head.lower() or head[0].islower():
        return ""
    if re.search(r"[?!.,]", head[:-1]):   # trailing period ok (e.g. "Acme Inc.")
        return ""
    if len(head.split()) > 5:             # company names are short; prose is not
        return ""
    return head


def _domain_from_text(text: str) -> str:
    for host in _URL_RE.findall(text):
        host = host.lower().removeprefix("www.")
        if "." in host and not _is_agg(host):
            return host
    return ""


async def _load_thread() -> list[dict]:
    """[{company, text, email, domain}] for the current hiring thread (cached)."""
    now = time.monotonic()
    if _cache["posts"] and now - _cache["at"] < _TTL:
        return _cache["posts"]

    posts: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": UA}) as client:
            r = await client.get(_ALGOLIA, params={
                "query": "who is hiring",
                "tags": "story,author_whoishiring",
                "hitsPerPage": 5,
            })
            stories = [
                h for h in (r.json().get("hits", []) if r.is_success else [])
                if (h.get("title") or "").startswith("Ask HN: Who is hiring?")
            ]
            if not stories:
                return _cache["posts"]  # serve stale over nothing
            sid = stories[0]["objectID"]

            # One page (100 newest posts) is ample and keeps the hunt fast.
            c = await client.get(_ALGOLIA, params={
                "tags": f"comment,story_{sid}",
                "numericFilters": f"parent_id={sid}",
                "hitsPerPage": 100, "page": 0,
            })
            for hit in (c.json().get("hits", []) if c.is_success else []):
                text = _clean(hit.get("comment_text"))
                if not text or _SEEKER_RE.search(text):
                    continue
                emails = [e.lower() for e in EMAIL_RE.findall(text)
                          if not _is_agg(e.split("@")[1])]
                posts.append({
                    "company": _company_from_post(text),
                    "text":    text,
                    "email":   emails[0] if emails else "",
                    "domain":  _domain_from_text(text),
                })
    except Exception:
        return _cache["posts"]

    if posts:
        _cache.update(at=now, posts=posts)
    return posts


class HackerNewsScraper(BaseScraper):
    name = "HackerNews"

    MAX = 12   # leads kept per hunt (latency + quality budget)

    async def search(self, query: str, **_) -> list[dict]:
        posts = await _load_thread()
        if not posts:
            return []
        company_mode = looks_like_company(query)

        leads: list[dict] = []
        seen_email: set[str] = set()
        seen_domain: set[str] = set()
        for p in posts:
            # Relevance: role queries match the post text; company queries match
            # the parsed company name.
            if company_mode:
                if not p["company"] or not company_matches(query, p["company"]):
                    continue
            elif not role_match(query, p["text"][:300]):
                continue

            company = p["company"] or "Unknown"
            ctx = f"From the HN 'Who is hiring' thread: {p['text'][:400]}"

            if p["email"]:
                if p["email"] in seen_email:
                    continue
                seen_email.add(p["email"])
                leads.append({
                    # Person-like locals only; role mailboxes get "" so the
                    # greeting falls back to "Hi," not "Hi Careers,".
                    "name":        person_name_from_email(p["email"], company),
                    "email":       p["email"],
                    "company":     company,
                    "designation": "Recruiter",
                    "source":      self.name,
                    "context":     ctx,
                })
            elif p["domain"] and p["domain"] not in seen_domain:
                seen_domain.add(p["domain"])
                leads.append({
                    "name":        "",
                    "email":       "",
                    "company":     company if company != "Unknown" else p["domain"].split(".")[0].title(),
                    "designation": "Recruiter",
                    "source":      self.name,
                    "context":     ctx,
                    "_domain":     p["domain"],
                })
            if len(leads) >= self.MAX:
                break
        return leads
