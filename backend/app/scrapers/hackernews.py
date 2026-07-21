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
from urllib.parse import unquote

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
    # Press/media/content sites — hiring posts link their funding coverage
    # ("we just raised, see the TechCrunch article"), and taking that URL as
    # the COMPANY domain grounded a real journalist's published email as a
    # "recruiter" (observed live: connie@techcrunch.com for a golang hunt).
    "techcrunch.com", "forbes.com", "businessinsider.com", "bloomberg.com",
    "reuters.com", "nytimes.com", "wsj.com", "theverge.com", "wired.com",
    "venturebeat.com", "theinformation.com", "crunchbase.com", "medium.com",
    "substack.com", "dev.to", "hackernoon.com", "producthunt.com",
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

# ── ATS slug harvesting ────────────────────────────────────────────────────────
# Hiring posts routinely link their own board ("apply: jobs.lever.co/acme/...").
# Each such URL is a VERIFIED company→ATS mapping — free directory growth on
# every hunt (a live probe of one month's harvest showed 47/47 boards alive
# with jobs). The slugs were previously thrown away by the _AGG filter.
_ATS_URL_RES: tuple[tuple[str, re.Pattern], ...] = (
    ("greenhouse",      re.compile(r"(?:boards|job-boards)\.(?:eu\.)?greenhouse\.io/([A-Za-z0-9_-]+)")),
    ("lever",           re.compile(r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_-]+)")),
    ("ashby",           re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9._%-]+)")),
    ("workable",        re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)")),
    ("smartrecruiters", re.compile(r"jobs\.smartrecruiters\.com/([A-Za-z0-9_-]+)")),
    ("recruitee",       re.compile(r"([A-Za-z0-9-]+)\.recruitee\.com")),
    ("breezy",          re.compile(r"([A-Za-z0-9-]+)\.breezy\.hr")),
)
# Path fragments that aren't board slugs, plus known shared talent-network /
# VC-portfolio boards (pear-vc: 86 jobs from MANY companies — attributing them
# to one poster's company poisons every lead from that board).
_JUNK_SLUGS = frozenset({
    "embed", "jobs", "boards", "www", "apply", "careers", "postings",
    "j", "job", "en", "share", "login", "pear-vc",
})


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t}


def _extract_ats_mappings(text: str) -> list[tuple[str, str]]:
    """Unique (ats, slug) pairs found in a post's cleaned text."""
    out: list[tuple[str, str]] = []
    for ats, rx in _ATS_URL_RES:
        for m in rx.finditer(text):
            # HN truncates long anchors with an ellipsis — strip the artifacts
            # the dots-allowed ashby charset would otherwise capture.
            slug = unquote(m.group(1)).lower().strip("./")
            if slug and slug not in _JUNK_SLUGS and (ats, slug) not in out:
                out.append((ats, slug))
    return out


def _mapping_from_post(text: str, ats: str, slug: str) -> dict:
    """Company name + domain for a harvested slug. The post's name/domain are
    trusted ONLY when the name agrees with the slug (token overlap) — a post
    about Phaselaw linking a shared pear-vc board must not stamp Phaselaw's
    name/domain onto that board (ats.py's domain_hint would then override the
    API's own domain and poison every lead from it)."""
    company = _company_from_post(text)
    domain = _domain_from_text(text)
    if not (company and _tokens(slug) & _tokens(company)):
        company = re.sub(r"[-._]+", " ", slug).title()
        domain = ""
    return {"ats": ats, "slug": slug, "company": company, "domain": domain}


def harvested_mappings() -> list[dict]:
    """ATS mappings from the cached thread scan (current + previous month)."""
    return list(_cache["mappings"])


# ── Founder self-identification ───────────────────────────────────────────────
# Many HN posts are written BY the founder ("I'm the co-founder, email me at
# ..."). Labelling that lead "Recruiter" routes it to the wrong email template
# and buries it in ranking. Applied ONLY to the embedded-email branch — the
# address the self-identifying author personally left. Two signals, highest
# precision first: the email's local part, then an explicit "I'm the founder"
# claim outside the "Company | Role | ..." header.
_FOUNDER_LOCAL_RE = re.compile(r"^(?:ceo|cto|coo|founder|founders|cofounder)@")
_FOUNDER_TEXT_RE = re.compile(
    r"i(?:'m| am) (?:the |a |one of the )?"
    r"(?:(?:md|ceo|cto|coo) and )?co-?founder"
    r"|i(?:'m| am) the (?:founder|ceo|cto|coo)"
    r"|i(?:'m| am) [a-z]+,? (?:co-?founder|founder|ceo|cto|coo) (?:of|at|and)",
    re.IGNORECASE,
)
# Never fire on these collocations (all observed live in the thread).
_FOUNDER_NEG_RE = re.compile(
    r"founding (?:engineer|designer|gtm|team|member|hire)"
    r"|looking for (?:a )?(?:technical )?co-?founder",
    re.IGNORECASE,
)


def _author_is_founder(text: str, email: str) -> bool:
    if _FOUNDER_LOCAL_RE.match(email or ""):
        return True
    # Search only past the "Company | Role | ..." header segment.
    body = text.split("|", 1)[-1]
    return bool(_FOUNDER_TEXT_RE.search(body)) and not _FOUNDER_NEG_RE.search(body)


# ── Per-process thread cache ───────────────────────────────────────────────────
# The thread is identical for every query in a month and changes slowly, so one
# fetch serves every hunt. Keyed nothing (global) with a 1h TTL; the monthly
# thread flip is picked up on the next refresh.
_TTL = 3600
_cache: dict = {"at": 0.0, "posts": [], "mappings": []}


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
    mappings: list[dict] = []
    seen_maps: set[tuple[str, str]] = set()

    def _harvest(text: str) -> None:
        for ats, slug in _extract_ats_mappings(text):
            if (ats, slug) not in seen_maps:
                seen_maps.add((ats, slug))
                mappings.append(_mapping_from_post(text, ats, slug))

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

            # The WHOLE thread in one call: monthly threads run ~300-600
            # top-level posts, comfortably under Algolia's 1000-hit ceiling.
            # Scanning only the newest 100 hid most of the thread from every
            # hunt (same response size class, same single request).
            c = await client.get(_ALGOLIA, params={
                "tags": f"comment,story_{sid}",
                "numericFilters": f"parent_id={sid}",
                "hitsPerPage": 1000, "page": 0,
            })
            for hit in (c.json().get("hits", []) if c.is_success else []):
                text = _clean(hit.get("comment_text"))
                if not text or _SEEKER_RE.search(text):
                    continue
                _harvest(text)
                emails = [e.lower() for e in EMAIL_RE.findall(text)
                          if not _is_agg(e.split("@")[1])]
                posts.append({
                    "company": _company_from_post(text),
                    "text":    text,
                    "email":   emails[0] if emails else "",
                    "domain":  _domain_from_text(text),
                })

            # Previous month's thread: harvested for SLUGS ONLY (never posts —
            # month-old postings must not resurface as leads). Own try/except:
            # a failure here must not discard the fresh current thread.
            try:
                if len(stories) >= 2:
                    sid2 = stories[1]["objectID"]
                    c2 = await client.get(_ALGOLIA, params={
                        "tags": f"comment,story_{sid2}",
                        "numericFilters": f"parent_id={sid2}",
                        "hitsPerPage": 1000, "page": 0,
                    })
                    for hit in (c2.json().get("hits", []) if c2.is_success else []):
                        text = _clean(hit.get("comment_text"))
                        if text and not _SEEKER_RE.search(text):
                            _harvest(text)
            except Exception:
                pass
    except Exception:
        return _cache["posts"]

    if posts:
        _cache.update(at=now, posts=posts, mappings=mappings)
    return posts


class HackerNewsScraper(BaseScraper):
    name = "HackerNews"

    # No lead cap: the whole thread is already fetched (and cached), so scanning
    # every post is free. A first-N cap in thread order made repeat hunts return
    # the same leads; downstream funnel caps bound the resolve work instead.

    async def search(self, query: str, *, query_variants: tuple = (), **_) -> list[dict]:
        posts = await _load_thread()
        if not posts:
            return []
        company_mode = looks_like_company(query)

        leads: list[dict] = []
        seen_email: set[str] = set()
        seen_domain: set[str] = set()
        for p in posts:
            # Relevance: role queries match the post text; company queries match
            # the parsed company name. A post matching only a sibling tech token
            # still emits, tagged _sibling (deprioritised downstream).
            sibling = False
            if company_mode:
                if not p["company"] or not company_matches(query, p["company"]):
                    continue
            elif not role_match(query, p["text"][:300]):
                if not (query_variants and
                        any(role_match(v, p["text"][:300]) for v in query_variants)):
                    continue
                sibling = True

            company = p["company"] or "Unknown"
            ctx = f"From the HN 'Who is hiring' thread: {p['text'][:400]}"

            if p["email"]:
                if p["email"] in seen_email:
                    continue
                seen_email.add(p["email"])
                lead = {
                    # Person-like locals only; role mailboxes get "" so the
                    # greeting falls back to "Hi," not "Hi Careers,".
                    "name":        person_name_from_email(p["email"], company),
                    "email":       p["email"],
                    "company":     company,
                    "designation": ("Founder" if _author_is_founder(p["text"], p["email"])
                                    else "Recruiter"),
                    "source":      self.name,
                    "context":     ctx,
                }
                if sibling:
                    lead["_sibling"] = True
                leads.append(lead)
            elif p["domain"] and p["domain"] not in seen_domain:
                seen_domain.add(p["domain"])
                lead = {
                    "name":        "",
                    "email":       "",
                    "company":     company if company != "Unknown" else p["domain"].split(".")[0].title(),
                    "designation": "Recruiter",
                    "source":      self.name,
                    "context":     ctx,
                    "_domain":     p["domain"],
                }
                if sibling:
                    lead["_sibling"] = True
                leads.append(lead)
        return leads
