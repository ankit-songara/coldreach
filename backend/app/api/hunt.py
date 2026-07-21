"""POST /api/hunt — runs all scrapers in parallel, resolves emails, saves results."""

import asyncio
import logging
import os
import random
import re
import time
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.crud import (
    ContactRepository, add_known_company,
    get_domain_patterns, record_domain_pattern,
    get_explored_slugs, record_explored_slugs,
    get_all_company_tags, upsert_company_tags,
)
from app.db.models import Contact, User
from app.deps import get_current_user
from app.schemas.contact import ContactCreate
from app.schemas.email import HuntRequest, HuntResult
from app.scrapers.base import (
    is_valid_email, person_name_from_email, ROLE_LOCALS,
    is_test_identity, plausible_person_name,
)
from app.scrapers.enricher import HunterEnricher
from app.scrapers.ats import (
    GreenhouseScraper, LeverScraper, AshbyScraper,
    SmartRecruitersScraper, RecruiteeScraper,
    WorkableScraper, BreezyScraper,
)
from app.scrapers.workday import WorkdayScraper
from app.scrapers.jobboards import (
    RemoteOKScraper, RemotiveScraper, ArbeitnowScraper,
    JobicyScraper, HimalayasScraper, TheMuseScraper, WeWorkRemotelyScraper,
    WorkingNomadsScraper, WorkableSearchScraper,
)
from app.scrapers.hackernews import HackerNewsScraper
from app.scrapers.yc import YCStartupsScraper
from app.scrapers.web import (
    emails_from_company_pages, find_published_role_email,
    search_role_email_on_web, HIRING_PREFIXES, GENERAL_PREFIXES,
)
from app.scrapers import directory
from app.scrapers.directory import looks_like_company, sibling_variants
from app.scrapers.resolver import (
    resolve as resolver_resolve, ResolutionCache, _smtp_probe,
)
from app.verifier import verify_email
from app.config import settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/hunt", tags=["hunt"])

# Phase budgets. The serverless function has a 60s maxDuration wall (frontend
# axios timeout is 65s), so the two network phases are individually bounded
# AND the resolve budget adapts to however long scraping actually took —
# profiled hunts showed the old flat 15s resolve budget was the top yield
# killer (~30% of leads left unresolved), while unbounded scraping could blow
# the total past the wall once the ATS scan breadth was widened.
_SCRAPE_BUDGET_SECONDS  = 18 if os.environ.get("VERCEL") else 40
_RESOLVE_BUDGET_SECONDS = 35 if os.environ.get("VERCEL") else 45
_TOTAL_HUNT_BUDGET_SECONDS = 52 if os.environ.get("VERCEL") else 120
_MIN_RESOLVE_SECONDS = 8    # floor: always give resolution a real chance
# Held back from the resolve budget for the (third) verify phase, and used as a
# hard deadline on it, so a flood of direct-email leads with slow/flaky MX
# lookups can't push scrape+resolve+verify past the 60s serverless wall (which
# would 504 and persist NOTHING despite a fully-successful scrape+resolve).
_VERIFY_RESERVE_SECONDS = 6
# "careers@" and "jobs@" are the most universally standard convention across
# company sizes and countries — tried first. "talent@"/"hr@" skew larger/tech-
# forward; "people@"/"team@" skew startup-specific and are the least reliable.
_ROLE_ADDRESSES = ("careers", "jobs", "hiring", "hr", "talent", "recruiting", "recruitment", "people", "team")

# Minimum confidence to persist a resolver-generated email. Direct scraper emails
# (confidence=0) are always kept; only resolver outputs are gated.
_MIN_RESOLVER_CONFIDENCE = 40

# Cap on P0 careers-inbox leads per hunt (one per unique company domain).
_MAX_CAREERS_LEADS = 30

# Every local part the pipeline can persist as a role-inbox contact — the P0
# probe list plus the grounding scan's hiring/general prefixes. Used to decide
# whether the user already OWNS a domain's role inbox (exclusion set).
_ROLEBOX_LOCALS = HIRING_PREFIXES | GENERAL_PREFIXES | frozenset(_ROLE_ADDRESSES)

# Per-user hunt rate limit: prevent rapid repeated scraping that could get the
# server IP blocked by ATS APIs.
_HUNT_COOLDOWN_SECONDS = 15
_last_hunt: dict[int, float] = {}

# Alternative TLDs to try when the guessed .com domain has no MX.
# Ordered by how common they are for tech companies; .in covers Indian
# companies, .org nonprofits/orgs. .com retried last as canonical fallback.
_ALT_TLDS = (".io", ".ai", ".co", ".app", ".dev", ".in", ".org", ".com")


# Freemail providers — an email here says nothing about the sender's company.
_FREEMAIL = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com",
    "live.com", "icloud.com", "me.com", "proton.me", "protonmail.com", "aol.com",
    "gmx.com", "gmx.de", "fastmail.com", "hey.com", "pm.me", "msn.com",
    "mail.com", "yandex.com", "zoho.com",
})


def _company_from_email(email: str) -> str:
    """Derive a display company name from a corporate email domain.
    'jobs@acme-labs.io' → 'Acme Labs'. Freemail domains yield ''."""
    domain = email.rsplit("@", 1)[-1].lower().strip()
    if not domain or "." not in domain or domain in _FREEMAIL:
        return ""
    labels = domain.split(".")
    base = labels[-2]
    # 'acme.co.uk' → labels[-2] is the ccTLD second level, step one label left.
    if base in ("co", "com", "org", "net", "ac", "gov", "edu") and len(labels) >= 3:
        base = labels[-3]
    pretty = re.sub(r"[-_]+", " ", base).strip().title()
    return pretty if len(pretty) > 1 else ""


def _desig_priority(designation: str) -> int:
    """Sort key. P0: grounded role inbox = 0. P1: Founder/CxO = 1, HR/TA = 2,
    Engineer = 3, other = 4. Unverified guesses = 5 (below every real lead —
    only reached if nothing else was found for that company)."""
    d = designation.lower()
    if "unverified guess" in d:
        return 5
    if "role inbox" in d:
        return 0
    if any(k in d for k in ("founder", "co-founder", "ceo", "cto", "chief", "founding")):
        return 1
    if any(k in d for k in ("hr", "human resource", "talent", "recruiter", "recruiting", "people ops", "people partner")):
        return 2
    if any(k in d for k in ("engineer", "developer", "swe", "software", "backend", "frontend", "fullstack", "devops", "data")):
        return 3
    return 4


# ── Query-relevance role filtering ────────────────────────────────────────────
# A contact's designation can belong to SEVERAL families (an "Engineering Manager"
# is both engineering and management), so membership is a set, not a single label.
# When the user picks a target role in the Hunt UI, we keep only the leads that
# match it — plus "gatekeepers" (founders/execs and recruiters), who are who you
# actually pitch regardless of the role — and drop clearly off-target individual
# contributors (e.g. a plain Software Engineer when you searched for management).
# Leads with no recognizable family are kept (we can't say they're off-target).
_FAMILY_PATTERNS: dict[str, "re.Pattern"] = {
    "founder_exec": re.compile(r"\b(founders?|co-?founders?|founding|ceo|cto|coo|cfo|cmo|cpo|chief|president|owner)\b", re.I),
    "recruiting":   re.compile(r"\b(recruit\w*|talent|sourcers?|people\s+(?:ops|partner|team)|human\s+resources?|hr|staffing)\b", re.I),
    "management":   re.compile(r"\b(managers?|management|managing|heads?|directors?|vp|vice\s+president|leads?|leadership)\b", re.I),
    "product":      re.compile(r"\b(product)\b", re.I),
    "design":       re.compile(r"\b(designers?|design|ux|ui|user\s+experience)\b", re.I),
    "data":         re.compile(r"\b(data|machine\s+learning|ml|ai|analytics|scientist)\b", re.I),
    "engineering":  re.compile(r"\b(engineer\w*|develop\w*|swe|software|backend|back-end|frontend|front-end|fullstack|full-?stack|devops|sre|site\s+reliability|platform|infrastructure|programmer|architect)\b", re.I),
}
# Always kept when a role filter is active — the universal outreach targets.
_GATEKEEPER_FAMILIES = frozenset({"founder_exec", "recruiting"})
# Valid values the API accepts for role_filter (anything else → treated as "any").
ROLE_FILTERS = frozenset(_FAMILY_PATTERNS.keys())


def _role_families(designation: str) -> set[str]:
    """Every role family a designation plausibly belongs to (may be empty)."""
    d = designation or ""
    return {fam for fam, pat in _FAMILY_PATTERNS.items() if pat.search(d)}


def _role_match_rank(designation: str, target: str) -> int | None:
    """Ranking/keep decision for a lead under an active role filter.
    Returns the sort rank (lower = more relevant), or None to DROP the lead:
      0 = designation matches the target family
      1 = a gatekeeper (founder/exec or recruiter)
      2 = no recognizable family (unknown — kept, but ranked last)
    """
    fams = _role_families(designation)
    if not fams:
        return 2                      # unknown role — don't assume it's off-target
    if target in fams:
        return 0
    if fams & _GATEKEEPER_FAMILIES:
        return 1
    return None                       # has a family, all off-target → drop


# Families checked first when inferring intent FROM THE QUERY TEXT ITSELF (as
# opposed to a contact's designation). A domain-specific term describes WHO to
# find; "management"/"founder_exec"/"recruiting" are usually just a seniority
# suffix riding along with it ("product manager" matches both "product" and
# "management" — the domain term should win, not the generic one).
_QUERY_DOMAIN_FAMILIES  = ("product", "design", "data", "engineering")
_QUERY_GENERIC_FAMILIES = ("management", "founder_exec", "recruiting")


def _infer_role_from_query(query: str) -> str:
    """
    Best-effort target-role family from the hunt query text, used ONLY when the
    user left the Hunt role dropdown on "Any role". Without this, typing
    "product" as the query did nothing by itself — the dropdown still had to be
    set separately, so a plain "product" search kept returning mostly engineers
    (whatever a job board happened to list), matching the query text but not
    the recipient's actual role. This makes the free-text query carry role
    intent on its own, same as picking the dropdown would.

    A domain-specific family match wins over a generic one when both appear
    ("product manager hiring" -> product, not management). Two domain-specific
    families matching at once ("product design lead") is genuinely ambiguous,
    so no filter is inferred rather than guessing wrong and dropping good leads.
    """
    fams = _role_families(query or "")
    if not fams:
        return ""
    domain = [f for f in _QUERY_DOMAIN_FAMILIES if f in fams]
    if len(domain) == 1:
        return domain[0]
    if domain:
        return ""   # ambiguous between two+ domain-specific families
    generic = [f for f in _QUERY_GENERIC_FAMILIES if f in fams]
    return generic[0] if len(generic) == 1 else ""


def _resolve_target_role(role_filter: str, query: str) -> str:
    """
    The role_filter actually used for this hunt. An explicit, valid dropdown
    pick always wins; only falls back to inferring from the query text when
    the caller sent nothing usable ('' / an unrecognised value). Returns ''
    when there's no signal anywhere — meaning no role filtering is applied.
    """
    explicit = (role_filter or "").strip().lower()
    if explicit in ROLE_FILTERS:
        return explicit
    # Inference is for free-text ROLE queries only. On a company-name hunt
    # ("Discovery", "Palo Alto Networks") a family word in the name would infer
    # a spurious filter and drop that company's own contacts — so never infer
    # when the query is a company name.
    if looks_like_company(query):
        return ""
    return _infer_role_from_query(query)


def _guess_company_domain(query: str) -> str:
    """
    Best-guess domain for a company-name hunt query, for the universal
    careers@/jobs@ fallback lead. Prefers the directory's REAL domain when the
    company is already known (curated seed or previously discovered); else
    guesses from the query text using the single best base slug.

    Real companies overwhelmingly use their short name as a domain, not the
    full legal name concatenated ("Acme Corp" -> acme.com, not acmecorp.com),
    so this prefers slugify_company's "first word alone" candidate over its
    "full name" one. Always guesses .com — TLD alternation (.io/.ai/.in/.org/…)
    happens downstream in _resolve_domain_contact via _find_live_domain when
    this guess has no MX. Returns '' if the query yields no usable slug.
    """
    known = directory.lookup(query)
    if known and known.domain:
        return known.domain
    slugs = directory.slugify_company(query)
    if not slugs:
        return ""
    base = slugs[1] if len(slugs) > 1 else slugs[0]
    return f"{base}.com" if base else ""


def _build_scrapers(hunter_key: str) -> list:
    scrapers = [
        GreenhouseScraper(),
        LeverScraper(),
        AshbyScraper(),
        SmartRecruitersScraper(),
        RecruiteeScraper(),
        WorkableScraper(),
        BreezyScraper(),
        WorkdayScraper(),
        RemoteOKScraper(),
        RemotiveScraper(),
        ArbeitnowScraper(),
        JobicyScraper(),
        HimalayasScraper(),
        TheMuseScraper(),
        WeWorkRemotelyScraper(),
        WorkingNomadsScraper(),
        WorkableSearchScraper(),
        HackerNewsScraper(),
        # Registered last: YC pool leads are tagged _pool and only fill the
        # funnel slots left over after organically-discovered leads.
        YCStartupsScraper(),
    ]
    key = hunter_key or settings.hunter_api_key
    if key:
        scrapers.append(HunterEnricher(key))
    return scrapers


def _personal_email(emails: list[str], domain: str) -> str | None:
    """Pick the most person-like, same-domain mailbox from scraped page emails."""
    domain = domain.lower()
    same = [e for e in emails if e.split("@")[-1] == domain]
    # Prefer addresses that look like a real name (first.last or a longer local).
    for e in same:
        local = e.split("@")[0]
        if local not in ROLE_LOCALS and ("." in local or len(local) > 4):
            return e
    # Otherwise any non-role mailbox at the domain.
    for e in same:
        if e.split("@")[0] not in ROLE_LOCALS:
            return e
    return None


async def _find_live_domain(company: str, guessed: str, cache: ResolutionCache) -> str:
    """
    When the guessed .com domain has no MX, try alternative TLDs common for tech companies.
    Returns the first domain with real MX records, or '' if none found.
    """
    base = guessed.rsplit(".", 1)[0]   # strip existing TLD
    # Try the hyphen-toggled base too, so we recover whichever spelling actually
    # resolves: "x-team" → x-team.com AND xteam.com, "foobar" → foobar.com AND
    # foo-bar isn't reconstructable so only the squashed form. Order-preserving,
    # de-duplicated. Every candidate is still MX-gated, so nothing ungrounded
    # is ever accepted.
    bases: list[str] = [base]
    if "-" in base:
        bases.append(base.replace("-", ""))
    for b in bases:
        for tld in _ALT_TLDS:
            candidate = b + tld
            if candidate == guessed:
                continue
            mx = await cache.mx(candidate)
            if mx:
                log.debug(f"Hunt: domain fallback {guessed} → {candidate} for '{company}'")
                return candidate
    return ""


async def _resolve_domain_contact(raw: dict, cache: ResolutionCache) -> dict | None:
    """
    Turn an identity-only lead (has _domain, may lack a name) into a contact with
    a real/best-guess email. Returns None if nothing usable could be resolved.
    """
    domain = raw.get("_domain", "")
    name   = (raw.get("name") or "").strip()
    if not domain:
        return None

    # If the guessed domain has no MX, try alternate TLDs before giving up.
    mx_check = await cache.mx(domain)
    if not mx_check:
        alt = await _find_live_domain(raw.get("company", ""), domain, cache)
        if alt:
            domain = alt
            raw = {**raw, "_domain": alt}
        else:
            return None

    # Named lead → full pattern-resolution + verification pipeline.
    # Guard: role-title names like "Lead Recruiter" / "Head of Engineering" have a
    # space but are not person names. Reject when either word is a known role token
    # so we don't generate nonsense patterns like "lead.recruiter@domain".
    _ROLE_TITLE_WORDS = frozenset({
        "lead", "head", "director", "manager", "recruiter", "coordinator",
        "specialist", "analyst", "associate", "executive", "officer", "founding",
    })
    if name and " " in name and not any(
        w.lower() in _ROLE_TITLE_WORDS for w in name.split()[:2]
    ):
        parts = name.split()
        resolved = await resolver_resolve(parts[0], parts[-1], domain, cache=cache)
        if not resolved:
            return None
        out = {**raw, "email": resolved.email, "confidence": resolved.confidence,
               "_domain": None,
               # Pattern provenance — harvested after the hunt into the
               # persistent pattern memory (underscore keys never persist).
               "_pattern": resolved.pattern,
               "_pattern_verified": resolved.verified}
        if resolved.catch_all:
            out["email_status"] = "risky"   # deliverable but unprovable
        return out

    # P0 careers-inbox leads: try to GROUND the address in real evidence before
    # ever falling back to a blind guess — a guessed "careers@domain" bounces
    # whenever the company actually uses hr@/jobs@/hiring@/etc instead.
    if raw.get("source") == "careers-inbox":
        published = await find_published_role_email(domain)
        if not published:
            # Not on the company's own pages (JS-rendered site, bot wall) —
            # try the wider web: job posts, directories, press pages.
            published = await search_role_email_on_web(domain, raw.get("company") or "")
        # Hunter's generic lookup is a secondary grounding signal when the
        # company's own pages / the web search find nothing published.
        if not published and settings.hunter_api_key:
            published = await HunterEnricher(settings.hunter_api_key).search_generic(domain)

        if published:
            prefix = published.split("@", 1)[0]
            # A dedicated hiring inbox (careers@/jobs@/hr@…) beats a general
            # company inbox (contact@/hello@/info@) — the designation picks the
            # email TEMPLATE, so a general inbox must be labelled a Company Inbox
            # (gets the company-inbox message), not Talent/Recruiting (which
            # writes a formal job application). Both are REAL published
            # addresses, so neither is the kind of blind guess that bounces.
            if prefix in HIRING_PREFIXES:
                desig, conf = "Talent/Recruiting (role inbox)", 70
            else:
                desig, conf = "Company Inbox (role inbox)", 60
            return {**raw, "email": published, "name": prefix.title(),
                    "designation": desig,
                    "confidence": conf, "email_status": "valid", "_domain": None}
    else:
        # No name → try the company's own pages for a real, named person.
        page_emails = await emails_from_company_pages(domain)
        personal = _personal_email(page_emails, domain)
        if personal:
            return {**raw, "email": personal,
                    "name": person_name_from_email(personal, raw.get("company") or ""),
                    "designation": raw.get("designation") or "Team",
                    "confidence": 50, "_domain": None}

    # No published address anywhere. NEVER invent one — every persisted email
    # must be grounded in real evidence (published page, web search, Hunter,
    # SMTP confirmation, or a catch-all domain that physically can't bounce).
    # Blind careers@ guesses caused a real production bounce storm; a company
    # with no findable address is simply dropped from the results.
    mx = await cache.mx(domain)
    if not mx:
        return None

    if await cache.catch_all(domain, mx):
        # Catch-all accepts EVERY local part — a conventional careers@ there
        # is deliverable by definition (it cannot bounce), so this is the one
        # case where using the convention isn't a guess about deliverability.
        prefix = _ROLE_ADDRESSES[0]
        return {**raw, "email": f"{prefix}@{domain}", "name": prefix.title(),
                "designation": "Talent/Recruiting (role inbox)",
                "confidence": 45, "email_status": "risky", "_domain": None}

    if os.environ.get("VERCEL"):
        # Outbound port 25 is blocked on Vercel, so _smtp_probe can never
        # confirm anything there (see resolver.py) — no grounding is possible,
        # so the lead is dropped rather than guessed.
        return None

    loop = asyncio.get_running_loop()
    for prefix in _ROLE_ADDRESSES:
        addr = f"{prefix}@{domain}"
        if await loop.run_in_executor(None, _smtp_probe, addr, mx[0]) is True:
            return {**raw, "email": addr, "name": prefix.title(),
                    "designation": "Talent/Recruiting (role inbox)",
                    "confidence": 55, "email_status": "risky", "_domain": None}
    return None


# ATS sources whose results encode a verified company→board mapping worth keeping.
_DISCOVERABLE_ATS = {"greenhouse", "lever", "ashby", "smartrecruiters", "recruitee",
                     "workable", "breezy"}


def _learn_companies(db: Session, results_per_scraper: list) -> None:
    """Persist genuinely-new company→ATS mappings found during a company-name hunt
    so the directory self-grows. Best-effort — never breaks a hunt."""
    seen: set[tuple[str, str]] = set()
    for results in results_per_scraper:
        for r in results:
            ats_name, sep, slug = (r.get("source") or "").partition("/")
            ats, slug = ats_name.strip().lower(), slug.strip()
            if not sep or ats not in _DISCOVERABLE_ATS or not slug:
                continue
            key = (ats, slug.lower())
            if key in seen or directory.is_known(ats, slug):
                continue   # already in the seed/runtime directory — nothing to learn
            seen.add(key)
            domain = r.get("_domain") or ""
            if not domain and "@" in (r.get("email") or ""):
                domain = r["email"].split("@", 1)[1]
            try:
                add_known_company(db, name=r.get("company") or slug, slug=slug,
                                   ats=ats, domain=domain, source="discovered")
                log.info(f"Hunt: learned new company {r.get('company') or slug} ({ats}/{slug})")
            except Exception:
                db.rollback()


# Cap on directory rows learned per hunt: each add is a SELECT+INSERT round
# trip to Supabase inside the 52s budget. The first post-deploy hunt sees the
# whole backlog (~50 mappings); the monthly trickle after that is ~40-70.
_MAX_LEARNED_PER_HUNT = 25


def _learn_from_hn(db: Session) -> None:
    """Persist company→ATS mappings harvested from the HN thread's apply links
    (the thread is already fetched — zero extra HTTP). Runs on EVERY hunt,
    unlike _learn_companies which only fires on company-name queries.
    Best-effort: never breaks a hunt."""
    from app.scrapers.hackernews import harvested_mappings
    learned = 0
    for m in harvested_mappings():
        if learned >= _MAX_LEARNED_PER_HUNT:
            break
        ats, slug = m.get("ats") or "", m.get("slug") or ""
        if ats not in _DISCOVERABLE_ATS or not slug or directory.is_known(ats, slug):
            continue
        try:
            add_known_company(db, name=m.get("company") or slug, slug=slug,
                               ats=ats, domain=m.get("domain") or "",
                               source="discovered")
            learned += 1
        except Exception:
            db.rollback()
    if learned:
        log.info(f"Hunt: learned {learned} companies from HN board links")


# ── Live "who's hiring" suggestions ──────────────────────────────────────────
# Company names with active engineering postings right now, for the Hunt page's
# suggestion chips — clicking one runs a company hunt directly. The FULL feed's
# matching companies are cached as a pool; each request serves a random sample
# from it, so chips change on every page load at zero extra fetch cost (the
# old first-12-in-feed-order approach showed the same chips all day). Cached
# module-level so the pool costs one RemoteOK fetch per process per TTL.
_SUGGEST_TTL_SECONDS = 900
_SUGGEST_POOL_MAX    = 80   # companies kept in the pool
_SUGGEST_SERVE       = 12   # companies returned per request
# "at" starts at -inf, NOT 0.0: time.monotonic() is time-since-boot, so on a
# freshly booted host (or a cold-started serverless microVM) 0.0 would read as
# "fetched < TTL ago" and the first request would serve [] without fetching.
_suggest_cache: dict = {"at": float("-inf"), "pool": []}

# RemoteOK company names arrive as filed: "LOTHIAN BUSES LIMITED", "Acme Pvt.
# Ltd." — legalese and shouting make the suggestion chips look like junk data.
_LEGAL_SUFFIX_RE = re.compile(
    r"[\s,]+(private\s+limited|pvt\.?\s*ltd\.?|limited|ltd\.?|llc|inc\.?|corp\.?|gmbh)\s*$",
    re.IGNORECASE,
)


# A suggestion chip's company must be hiring for an ENGINEERING role — the
# chip carries the role as its reason to click, so a non-eng role is noise.
_ENG_ROLE_RE = re.compile(
    r"\b(engineer|engineering|developer|devops|sre|sde|swe|architect|"
    r"programmer|scientist|golang|python|react|typescript|node|java|rust|"
    r"kubernetes|frontend|backend|fullstack|ios|android)\b"
)

_SENIORITY_RE = re.compile(
    r"^(?:senior|staff|lead|principal|junior|jr\.?|sr\.?|mid[- ]level|head of)\s+",
    re.IGNORECASE,
)


def _short_role(position: str) -> str:
    """Compress a listing title into a chip-sized hint: strip seniority
    prefixes and parenthetical/comma tails. "Senior Backend Engineer (Go),
    Platform" → "Backend Engineer (Go)" → capped."""
    import html as _html
    pos = " ".join(_html.unescape(position or "").split())
    pos = re.split(r",|\||—|–| - ", pos)[0].strip()
    pos = _SENIORITY_RE.sub("", pos).strip()
    if len(pos) > 28:
        # Cut at a word boundary — "Tech Lead Full-Stack Rails E" reads worse
        # than "Tech Lead Full-Stack".
        pos = pos[:28].rsplit(" ", 1)[0]
    return pos.rstrip(" (,-") if pos else ""


def _display_company(raw: str) -> str:
    import html as _html
    # Feed names arrive HTML-escaped ("Rose, Klein &amp; Marias") and sometimes
    # double-encoded ("CasinÃ² Lugano" for "Casinò Lugano") — repair both so
    # chips never show entities or mojibake.
    name = _html.unescape(raw)
    if "Ã" in name or "â€" in name:
        try:
            name = name.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    name = " ".join(name.split())
    name = _LEGAL_SUFFIX_RE.sub("", name).strip(" .,|-")
    if name.isupper() and len(name) > 4:   # keep real acronyms (IBM, SAP) intact
        name = name.title()
    return name


@router.get("/suggestions")
async def hunt_suggestions(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    now = time.monotonic()
    if now - _suggest_cache["at"] > _SUGGEST_TTL_SECONDS:
        pool: list[dict] = []
        seen: set[str] = set()

        def _admit(company_raw: str, position: str) -> None:
            comp = _display_company(company_raw or "")
            pos = (position or "").lower()
            # Engineering postings only — the POSITION must name an eng role
            # noun or an unambiguous tech token (word-bounded). Bare substrings
            # admitted "Data Entry Assistant" (data); tag matches admitted
            # "Social Media Content Creator".
            if (comp and comp.lower() not in seen and 2 < len(comp) <= 30
                    and len(pool) < _SUGGEST_POOL_MAX
                    and _ENG_ROLE_RE.search(pos)):
                seen.add(comp.lower())
                pool.append({"name": comp, "role": _short_role(position or "")})

        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(
                timeout=8, follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as client:
                # Remotive first: its software-dev category returns ONLY dev
                # listings, so the pool is all engineering. RemoteOK's default
                # feed is every category (measured: 99 listings, 1 eng title)
                # — kept as a top-up, same eng-title gate.
                try:
                    r = await client.get(
                        "https://remotive.com/api/remote-jobs",
                        params={"category": "software-dev", "limit": 150},
                    )
                    for j in ((r.json().get("jobs") if r.is_success else []) or []):
                        if isinstance(j, dict):
                            _admit(j.get("company_name"), j.get("title"))
                except Exception as e:
                    log.debug(f"Suggestions: Remotive fetch failed: {e}")
                if len(pool) < _SUGGEST_SERVE * 2:
                    r = await client.get("https://remoteok.com/api")
                    if r.is_success:
                        for j in r.json():
                            if isinstance(j, dict):
                                _admit(j.get("company"), j.get("position"))
        except Exception as e:
            log.debug(f"Suggestions: pool refresh failed: {e}")
        # Serve stale data over nothing if the refresh failed.
        if pool:
            _suggest_cache.update(at=now, pool=pool)
        else:
            _suggest_cache["at"] = now - _SUGGEST_TTL_SECONDS + 60  # retry in 1 min

    pool = _suggest_cache["pool"]
    # Personalise: a chip for a company the user already has contacts at is a
    # near-dead click — prefer companies not yet in their list. Applied
    # per-request AFTER the cache read: the pool is shared across users.
    try:
        owned = {
            (c or "").lower()
            for (c,) in db.query(Contact.company)
                          .filter(Contact.user_id == user.id).distinct()
        } - {"", "unknown"}
    except Exception:
        owned = set()
    fresh = [c for c in pool if c["name"].lower() not in owned]
    # A power user may own contacts at every pooled company — live chips still
    # beat an empty row, so fall back to the unfiltered pool.
    base = fresh or pool
    k = min(_SUGGEST_SERVE, len(base))
    sample = random.sample(base, k) if k else []
    return {
        # Names alone kept for older cached bundles; hiring_now carries the
        # role hint so a chip can say WHY the company appears ("Dosed" alone
        # reads as junk — "Dosed — Backend Engineer" is a reason to click).
        "hiring_companies": [c["name"] for c in sample],
        "hiring_now": sample,
    }


@router.post("", response_model=HuntResult)
async def hunt(req: HuntRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Multi-source email hunt with pattern-resolution + SMTP verification.

    Flow:
    1. ATS/job-board scrapers run in parallel (Greenhouse, Lever, Ashby, …, Hunter)
    2. P0: every discovered company domain yields a careers@/jobs@ role-inbox lead
    3. P1: contacts with emails validated directly; identity-only leads go through
       the resolver pipeline (pattern learning + SMTP probe)
    4. All resolved contacts saved — role inboxes first, then founders, HR/TA,
       then role-relevant people
    """
    # Rate limit: one hunt per user per cooldown window.
    now = time.monotonic()
    # -inf, not 0: monotonic() is time-since-boot, so 0 would 429 a user's very
    # first hunt during the first cooldown-window after a (micro-VM) boot.
    last = _last_hunt.get(user.id, float("-inf"))
    if now - last < _HUNT_COOLDOWN_SECONDS:
        wait = int(_HUNT_COOLDOWN_SECONDS - (now - last)) + 1
        raise HTTPException(429, f"Please wait {wait}s before hunting again.")
    _last_hunt[user.id] = now
    if len(_last_hunt) > 512:   # prune expired entries so the map can't grow unbounded
        cutoff = now - _HUNT_COOLDOWN_SECONDS
        for uid in [u for u, t in _last_hunt.items() if t < cutoff]:
            _last_hunt.pop(uid, None)

    log.info(f"Hunt: {req.query!r}")
    hunt_t0 = time.monotonic()

    # ── Exclusion set: everything the user already owns ────────────────────────
    # Loaded up front (one two-column SELECT) so the pipeline can skip
    # already-owned leads BEFORE spending resolve/SMTP budget on them. Without
    # this, a repeat hunt spent its whole budget re-discovering the user's
    # existing list (dedup only happened at persist time) and saved nothing —
    # while excluded leads also no longer consume resolve slots, so each
    # re-hunt digs deeper into the same sources instead.
    repo = ContactRepository(db, user.id)
    owned_pairs = repo.all_email_names()
    owned_emails: set[str] = {e for e, _ in owned_pairs}
    # Domains where the user already owns the ROLE-INBOX contact itself
    # (careers@/jobs@/contact@ …). Only these suppress the P0 careers@ probe
    # and nameless identity leads — owning a PERSON at a domain must not
    # suppress a genuinely-new careers@ lead there, and vice versa.
    owned_roleinbox_domains: set[str] = set()
    for e in owned_emails:
        local, _, dom = e.partition("@")
        if dom and dom not in _FREEMAIL and local in _ROLEBOX_LOCALS:
            owned_roleinbox_domains.add(dom)
    skipped_known = 0

    scrapers = _build_scrapers(req.hunter_api_key)

    # ── Exploration cursor + sibling expansion (role queries only) ─────────────
    # The cursor remembers which ATS boards this user's repeat hunts already
    # probed for this query, so each re-run covers a FRESH directory slice.
    # Company hunts bypass it — a targeted hunt must always probe that company.
    # Sibling variants re-filter the same fetched feeds for related tech
    # (backend → golang/python/…) at zero extra HTTP; matches are tagged
    # _sibling and never displace primary matches downstream.
    company_query = looks_like_company(req.query)
    query_norm = " ".join(req.query.lower().split())[:255]
    explored: frozenset = frozenset()
    variants: tuple = ()
    query_tokens: frozenset = frozenset()
    if not company_query:
        try:
            explored = frozenset(get_explored_slugs(db, user.id, query_norm))
        except Exception as e:
            log.debug(f"Hunt: cursor read skipped: {e}")
        variants = tuple(sibling_variants(req.query))
        # Refresh the tag overlay (one SELECT) and derive the query's tech
        # tokens (with aliases + siblings) so ATS probing ranks tag-matching
        # companies first instead of drawing blind from a growing directory.
        try:
            for (a, s), tags in get_all_company_tags(db).items():
                directory.set_company_tags(a, s, tags)
        except Exception as e:
            log.debug(f"Hunt: tag overlay load skipped: {e}")
        toks = {k for k in directory.role_keywords(req.query)
                if k in directory._TECH_TOKENS}
        for t in list(toks):
            toks |= directory._TECH_ALIASES.get(t, set())
        toks |= set(variants)
        query_tokens = frozenset(toks)
    probed: list[tuple] = []   # (ats_key, slug, n_leads, board_tags) per completed fetch

    # Sources hit distinct hosts, so run them fully concurrently (no
    # staggering) — but bounded: one slow board must not eat the wall-clock
    # budget the resolve phase needs. Completed scrapers are harvested; the
    # stragglers are cancelled and count as empty.
    scrape_tasks = [asyncio.create_task(s.safe_search(
        req.query, explored_slugs=explored, probed_out=probed,
        query_variants=variants, query_tokens=query_tokens,
    )) for s in scrapers]
    done_scrape, pending_scrape = await asyncio.wait(scrape_tasks, timeout=_SCRAPE_BUDGET_SECONDS)
    if pending_scrape:
        for t in pending_scrape:
            t.cancel()
        await asyncio.gather(*pending_scrape, return_exceptions=True)
        slow = [s.name for s, t in zip(scrapers, scrape_tasks) if t in pending_scrape]
        log.info(f"Hunt: scrape budget hit — dropped slow sources: {', '.join(slow)}")
    results_per_scraper = [
        t.result() if (t in done_scrape and not t.cancelled() and t.exception() is None) else []
        for t in scrape_tasks
    ]
    log.info(f"Hunt: scrape phase took {time.monotonic() - hunt_t0:.1f}s")

    # Self-grow the directory: a company-name query that resolved on a real ATS
    # board teaches us a new company→board mapping for everyone's future hunts.
    if looks_like_company(req.query):
        try:
            _learn_companies(db, results_per_scraper)
        except Exception as e:
            log.debug(f"Hunt: company-learning skipped: {e}")
    # HN apply-link harvest runs on EVERY hunt — the thread was just fetched.
    try:
        _learn_from_hn(db)
    except Exception as e:
        log.debug(f"Hunt: HN slug learning skipped: {e}")

    # ── Split: known-email contacts vs identity-only (need resolution) ─────────
    seen_emails: set[str] = set()
    with_email:  list[dict] = []
    needs_resolve: list[dict] = []
    source_counts: dict[str, int] = {}
    # Domains of skipped owned DIRECT emails still seed the careers@ derivation
    # below — owning priya@acme.com must not cost acme.com its careers@ probe.
    skipped_domain_company: dict[str, str] = {}
    skipped_nameless_domains: set[str] = set()
    skipped_owned_emails: set[str] = set()

    dropped_junk = 0
    for scraper, results in zip(scrapers, results_per_scraper):
        count = 0
        for r in results:
            # Test fixtures masquerading as people ("Test User", "John Doe",
            # "root") are junk regardless of how good their email looks.
            if is_test_identity(r.get("name") or ""):
                dropped_junk += 1
                continue
            email = (r.get("email") or "").lower().strip()
            if email:
                if email not in seen_emails and is_valid_email(email):
                    seen_emails.add(email)
                    count += 1   # a real hiring signal either way → counts as "found"
                    if email in owned_emails:
                        # Already in the user's list — skipped once per unique
                        # address (seen_emails guards multi-board repeats).
                        skipped_known += 1
                        skipped_owned_emails.add(email)
                        d = email.rsplit("@", 1)[-1]
                        if d not in _FREEMAIL and d not in skipped_domain_company:
                            skipped_domain_company[d] = (r.get("company") or "").strip()
                        continue
                    with_email.append({**r, "email": email, "confidence": r.get("confidence", 0)})
            elif r.get("_domain"):
                # A NAMELESS identity lead resolves to the domain's role inbox —
                # if the user already OWNS that role inbox it's a guaranteed
                # duplicate; skip before it costs a resolve slot. NAMED people
                # always stay: the resolver can find a new person anywhere
                # (save-time dedup catches exact repeats).
                d = (r.get("_domain") or "").lower().strip()
                if d in owned_roleinbox_domains and not (r.get("name") or "").strip():
                    if d not in skipped_nameless_domains:
                        skipped_nameless_domains.add(d)
                        skipped_known += 1
                    count += 1
                    continue
                needs_resolve.append(r)
                count += 1
        source_counts[scraper.name] = count
    if dropped_junk:
        log.info(f"Hunt: dropped {dropped_junk} test-identity leads")

    # ── P0: careers@/jobs@ role-inbox lead for EVERY company discovered ────────
    # The primary product output of a hunt. One synthetic lead per unique
    # corporate domain, gathered from every source: identity-only leads,
    # direct-email leads, and (for company-name queries) the query itself.
    # Resolved via the fast path in _resolve_domain_contact — no page-scrape,
    # TLD alternation when the guess has no MX. Kept in a SEPARATE list so a
    # domain can yield BOTH its careers@ inbox (P0) and a named person (P1).
    # Primary-match leads first so sibling-only domains can't consume the
    # careers-lead cap ahead of them; directory-pool leads (YC) fill only
    # LEFTOVER slots (stable sort keeps feed order otherwise).
    needs_resolve.sort(key=lambda r: 2 if r.get("_pool") else (1 if r.get("_sibling") else 0))

    # Domains where the user already OWNS the role inbox are skipped — the
    # careers@ probe would only prove a duplicate. Owning a mere person at a
    # domain does NOT suppress its careers@ probe (that lead is new).
    skipped_careers_domains: set[str] = set()
    domain_company: dict[str, str] = {}
    for r in needs_resolve:
        d = (r.get("_domain") or "").lower().strip()
        if d in owned_roleinbox_domains:
            skipped_careers_domains.add(d)
            continue
        if d and d not in _FREEMAIL and d not in domain_company:
            domain_company[d] = (r.get("company") or "").strip()
    for r in with_email:
        d = r["email"].rsplit("@", 1)[-1]
        if d in owned_roleinbox_domains:
            skipped_careers_domains.add(d)
            continue
        if d and d not in _FREEMAIL and d not in domain_company:
            domain_company[d] = (r.get("company") or "").strip()
    # Domains seen only via skipped OWNED direct emails still get their probe
    # (unless their role inbox is what's owned).
    for d, comp in skipped_domain_company.items():
        if d not in owned_roleinbox_domains and d not in domain_company:
            domain_company[d] = comp
    if looks_like_company(req.query):
        guess = _guess_company_domain(req.query)
        if guess and guess not in domain_company and guess not in owned_roleinbox_domains:
            domain_company[guess] = req.query.strip()
    skipped_known += len(skipped_careers_domains - skipped_nameless_domains)

    careers_leads = [
        {"name": "", "company": comp or _company_from_email(f"x@{dom}"),
         "designation": "", "source": "careers-inbox", "_domain": dom}
        for dom, comp in list(domain_company.items())[:_MAX_CAREERS_LEADS]
    ]
    if careers_leads:
        source_counts["careers-inbox"] = len(careers_leads)

    log.info(f"Hunt: {len(with_email)} direct emails, {len(careers_leads)} P0 careers leads, "
             f"{len(needs_resolve)} identity-only leads, {skipped_known} skipped as already-owned")

    # ── Seed a shared cache with every real email found, so cross-source pattern
    #    learning works for free (e.g. GitHub emails at acme.com teach acme.com's
    #    pattern, applied to an ATS recruiter lead at the same domain). ──────────
    cache = ResolutionCache()
    for r in with_email:
        cache.observe(r["email"], r.get("name") or "")
    # Owned contacts are grounded pattern evidence too (observe() ignores
    # role inboxes itself — it requires a real "First Last" name).
    for e, n in owned_pairs:
        cache.observe(e, n)

    # ── Dedupe identity-only leads by domain across sources (several boards list
    #    the same company) so the resolve budget isn't spent twice on one domain.
    #    A named lead wins over a nameless one for the same domain, and named leads
    #    resolve first — they yield a real person at high confidence, while nameless
    #    leads cost a page-scrape + role-probe for a lower-value role inbox. ──────
    by_domain: dict[str, dict] = {}
    for r in needs_resolve:
        d = (r.get("_domain") or "").lower()
        cur = by_domain.get(d)
        if cur is None:
            by_domain[d] = r
        else:
            # A named lead wins over a nameless one; otherwise keep the existing entry
            # but MERGE the richer context so the email generator keeps all real facts
            # (e.g. HN post salary/stack data survives even when an ATS lead arrives
            # first with a name but an empty context field).
            cur_named = bool((cur.get("name") or "").strip())
            new_named  = bool((r.get("name") or "").strip())
            # A domain matched by BOTH a primary and a sibling lead is primary.
            if cur.get("_sibling") and not r.get("_sibling"):
                cur.pop("_sibling", None)
            if not cur_named and new_named:
                # Incoming has a name — adopt it, merge context from the old entry
                merged_ctx = " ".join(
                    filter(None, [r.get("context"), cur.get("context")])
                )[:2000]
                merged = {**r, "context": merged_ctx or r.get("context")}
                if not cur.get("_sibling"):
                    merged.pop("_sibling", None)
                by_domain[d] = merged
            else:
                # Keep existing name; supplement context if the new entry has more
                existing_ctx = (cur.get("context") or "")
                extra_ctx    = (r.get("context") or "")
                if extra_ctx and extra_ctx not in existing_ctx:
                    merged_ctx = (existing_ctx + "\n" + extra_ctx).strip()[:2000]
                    by_domain[d] = {**cur, "context": merged_ctx}
    # Primaries before siblings before pool leads, then named before nameless:
    # lower-relevance bands must never displace a primary from the resolve
    # slice or the careers cap.
    needs_resolve = sorted(
        by_domain.values(),
        key=lambda r: (
            2 if r.get("_pool") else (1 if r.get("_sibling") else 0),
            0 if " " in (r.get("name") or "").strip() else 1,
        ),
    )

    # ── Pattern memory: seed the cache with formats learned in PREVIOUS hunts
    #    (one DB query) so those domains guess right on the first candidate
    #    instead of re-learning via GitHub search / SMTP probes. ────────────────
    try:
        stored = get_domain_patterns(db, [
            (r.get("_domain") or "").lower() for r in needs_resolve
        ])
        for dom, patt in stored.items():
            cache.seed_pattern(dom, patt)
        if stored:
            log.info(f"Hunt: seeded {len(stored)} domain patterns from memory")
    except Exception as e:
        log.debug(f"Hunt: pattern preload skipped: {e}")

    # ── Resolve identity-only leads — concurrency-limited, with a time budget so
    #    a blocked port 25 can't make the hunt hang. ─────────────────────────────
    # Network-bound work (DNS, 2 small page fetches) — wide enough that all P0
    # careers leads clear the budget now that each one costs a grounding scan.
    semaphore = asyncio.Semaphore(10)

    async def guarded_resolve(raw: dict) -> dict | None:
        async with semaphore:
            return await _resolve_domain_contact(raw, cache)

    # P0 careers leads first — they're the hunt's primary output and their
    # grounding scan is capped tight (2 concurrent fetches, ~4s), so they
    # must land inside the time budget.
    # deepen widens BREADTH only (never time): 20 extra resolve candidates are
    # cancelled at the same deadline, not given more of the 60s wall.
    resolve_slots = 60 if req.deepen else 40
    tasks = [asyncio.create_task(guarded_resolve(r))
             for r in careers_leads + needs_resolve[:resolve_slots]]
    if tasks:
        # Adaptive: whatever wall-clock the scrape phase consumed comes out of
        # the resolve budget so the total stays inside the serverless limit.
        # _VERIFY_RESERVE_SECONDS is held back so the (third) verify phase can't
        # be starved into breaching the 60s wall — the resolve budget must NOT
        # be allowed to consume the whole total.
        resolve_budget = max(
            _MIN_RESOLVE_SECONDS,
            min(_RESOLVE_BUDGET_SECONDS,
                _TOTAL_HUNT_BUDGET_SECONDS - _VERIFY_RESERVE_SECONDS
                - (time.monotonic() - hunt_t0)),
        )
        done, pending = await asyncio.wait(tasks, timeout=resolve_budget)
        if pending:
            for t in pending:
                t.cancel()
            # Await the cancelled tasks so they can run their finally/cleanup blocks
            # and release semaphore slots and open connections before we return.
            await asyncio.gather(*pending, return_exceptions=True)
            log.info(f"Hunt: resolve budget hit — {len(pending)} leads left unresolved")

        for t in done:
            if t.cancelled() or t.exception() is not None:
                continue
            r = t.result()
            if not r:
                continue
            email = (r.get("email") or "").lower().strip()
            conf = r.get("confidence", 0)
            # Skip low-confidence resolver guesses — they likely bounce and hurt
            # sender reputation. Direct scraper emails (confidence=0) bypassed this.
            if conf > 0 and conf < _MIN_RESOLVER_CONFIDENCE:
                log.debug(f"Hunt: dropping {email} (confidence {conf} < {_MIN_RESOLVER_CONFIDENCE})")
                continue
            if email and email not in seen_emails and is_valid_email(email):
                seen_emails.add(email)
                # The resolver can re-derive an address the user already owns
                # (same pattern, same person) — skip it here so it never costs
                # a verify slot or shows up as a save-time duplicate.
                if email in owned_emails:
                    skipped_known += 1
                    skipped_owned_emails.add(email)
                    continue
                with_email.append(r)

    # ── Persist pattern memory for future hunts: SMTP-verified resolutions are
    #    strong confirmations, everything else the cache learned is a weak
    #    observation. Best-effort — never breaks a hunt. ─────────────────────────
    try:
        recorded: set[str] = set()
        for r in with_email:
            patt = r.get("_pattern")
            if patt and r.get("email"):
                dom = r["email"].rsplit("@", 1)[-1].lower()
                record_domain_pattern(db, dom, patt, bool(r.get("_pattern_verified")))
                recorded.add(dom)
        for dom, patt in cache.learned_patterns().items():
            if dom not in recorded:
                record_domain_pattern(db, dom, patt, verified=False)
    except Exception as e:
        log.debug(f"Hunt: pattern persistence skipped: {e}")

    # ── Verify deliverability inline (syntax + MX + disposable/role heuristics).
    #    Drop invalid addresses entirely so they never reach the user or hurt the
    #    sending account's reputation; tag the rest so the UI can warn. A "risky"
    #    verdict already set upstream (catch-all / role inbox) is preserved.
    #    Run in the default executor so slow MX lookups never block the event loop
    #    (a synchronous pool.map here froze the whole server for other users). ────
    loop = asyncio.get_running_loop()
    # Leads with a trusted preset ("valid"/"risky", set by the resolver from real
    # grounding) keep it regardless of the cheap verifier — so skip their MX
    # lookup entirely. Only leads without a trusted preset are verified, which
    # cuts the executor fan-out on the common direct-email flood.
    pairs = [(r, loop.run_in_executor(None, verify_email, r["email"]))
             for r in with_email if r.get("email_status") not in ("risky", "valid")]
    if pairs:
        # Hard deadline from remaining wall time: a flood of direct-email leads
        # with slow/flaky MX lookups must NOT push scrape+resolve+verify past the
        # 60s serverless wall — that would 504 and persist nothing.
        verify_deadline = max(3.0, _TOTAL_HUNT_BUDGET_SECONDS - (time.monotonic() - hunt_t0))
        await asyncio.wait([f for _, f in pairs], timeout=verify_deadline)

    done_verdict = {
        id(r): f.result()
        for r, f in pairs
        if f.done() and not f.cancelled() and f.exception() is None
    }

    verified: list[dict] = []
    dropped_invalid = 0
    for r in with_email:
        preset = r.get("email_status")
        if preset in ("risky", "valid"):
            verified.append(r)            # trusted grounding — no verify needed
            continue
        verdict = done_verdict.get(id(r))  # None if verify didn't finish in time
        if verdict == "invalid":
            dropped_invalid += 1           # only DROP on a definite invalid verdict
            continue
        # A lead that couldn't be verified in time keeps "unknown" — never
        # dropped (we don't lose a grounded lead to a slow DNS server) and
        # never invented (an unverifiable address is not upgraded to valid).
        r["email_status"] = verdict or preset or "unknown"
        verified.append(r)
    with_email = verified
    if dropped_invalid:
        log.info(f"Hunt: dropped {dropped_invalid} undeliverable addresses")

    # ── Query relevance: when the user picked a target role, keep only leads
    #    that match it (plus gatekeepers), dropping off-target ICs. Ranked leads
    #    then sort by that relevance first. ─────────────────────────────────────
    _status_rank = {"valid": 0, "unknown": 1, "risky": 2}
    target = _resolve_target_role(req.role_filter, req.query)
    if target and target != (req.role_filter or "").strip().lower():
        log.info(f"Hunt: inferred role filter '{target}' from query {req.query!r}")
    role_filtered = 0
    if target in ROLE_FILTERS:
        ranked: list[tuple[int, dict]] = []
        for r in with_email:
            rank = _role_match_rank(r.get("designation") or "", target)
            if rank is not None:
                ranked.append((rank, r))
        role_filtered = len(with_email) - len(ranked)
        if role_filtered:
            log.info(f"Hunt: role filter '{target}' dropped {role_filtered} off-target leads")
        # Careers inboxes stay first even under a role filter — they're the P0
        # product output regardless of which people the filter targets.
        ranked.sort(key=lambda pr: (
            0 if "role inbox" in (pr[1].get("designation") or "").lower() else 1,
            pr[0],                                                     # role relevance
            _status_rank.get(pr[1].get("email_status") or "unknown", 1),
            -(pr[1].get("confidence") or 0),
        ))
        with_email = [r for _, r in ranked]
    else:
        # P0 careers inboxes → Founders → HR/TA → Engineers → rest; within each
        # tier, verified emails before risky/unknown, then higher confidence.
        with_email.sort(key=lambda r: (
            _desig_priority(r.get("designation") or ""),
            _status_rank.get(r.get("email_status") or "unknown", 1),
            -(r.get("confidence") or 0),
        ))

    # ── Persist ────────────────────────────────────────────────────────────────
    # "Unknown" company leaks straight into generated emails ("at Unknown") — when
    # the scraper couldn't name the company, derive it from the email's domain.
    # Names get the same treatment: scrapers return git author strings, handles,
    # and org names — anything that isn't plausibly a person is replaced by a
    # name derived from the email ("sarah.chen@…" → "Sarah Chen") or the
    # "Contact" sentinel, so the UI and the email greeting never treat
    # "dev4life" or "Acme Careers" as somebody's name.
    def _clean_identity(r: dict) -> tuple[str, str]:
        company = (
            (c if (c := (r.get("company") or "").strip()) and c != "Unknown" else "")
            or _company_from_email(r["email"]) or "Unknown"
        )
        raw_name = (r.get("name") or "").strip()
        if plausible_person_name(raw_name, company):
            name = raw_name
        else:
            name = person_name_from_email(r["email"], company) or "Contact"
            # The email-derived guess must clear the same bar (e.g. 'acmehr@…').
            if name != "Contact" and not plausible_person_name(name, company):
                name = "Contact"
        return name, company

    contacts_to_save = []
    for r in with_email:
        name, company = _clean_identity(r)
        contacts_to_save.append(ContactCreate(
            name         = name,
            email        = r["email"],
            designation  = r.get("designation") or "Hiring Manager",
            company      = company,
            source       = r.get("source") or "",
            context      = r.get("context") or None,
            confidence   = r.get("confidence") or 0,
            email_status = r.get("email_status") or "unknown",
        ))
    saved, save_time_existing = repo.bulk_create(contacts_to_save)
    log.info(f"Hunt complete: {len(saved)} new contacts saved")

    # ── Which existing contacts made leads duplicates? ─────────────────────────
    # Hydrated from the user's contact list in ONE fetch: exact-email skips map
    # directly; domain-level skips (nameless/careers) map to a representative
    # owned contact at that domain (role-inbox contact preferred, since that is
    # what the skipped probe would have found). Deduped by id, capped — the
    # list is illustrative, so its length may not equal the duplicates count.
    duplicate_contacts: list[dict] = []
    try:
        seen_dup_ids: set[int] = set()
        skipped_domains = skipped_nameless_domains | skipped_careers_domains
        if skipped_owned_emails or skipped_domains or save_time_existing:
            all_owned = repo.get_all()
            by_owned_email = {c.email.lower(): c for c in all_owned if c.email}
            def _add(c) -> None:
                if c is not None and c.id not in seen_dup_ids and len(duplicate_contacts) < 25:
                    seen_dup_ids.add(c.id)
                    duplicate_contacts.append({
                        "id": c.id, "name": c.name, "company": c.company,
                        "email": c.email, "status": c.status,
                    })
            for em in sorted(skipped_owned_emails):
                _add(by_owned_email.get(em))
            for c in save_time_existing:
                _add(c)
            for dom in sorted(skipped_domains):
                at_domain = [c for c in all_owned
                             if c.email and c.email.lower().endswith("@" + dom)]
                if at_domain:
                    rolebox = [c for c in at_domain
                               if c.email.split("@", 1)[0].lower() in _ROLEBOX_LOCALS]
                    _add((rolebox or at_domain)[0])
    except Exception as e:
        log.debug(f"Hunt: duplicate-contact hydration skipped: {e}")

    # ── Exploration cursor write-back ──────────────────────────────────────────
    # A slug is "explored" only on a definitive outcome: its fetch completed
    # with no matching roles, OR its leads actually reached the persist stage.
    # Leads dropped mid-pipeline (resolve cancel, slot caps, confidence floor,
    # verify-invalid, role filter) never mark their board explored — the user
    # never received them, so a future hunt must retry.
    if not company_query and probed:
        try:
            persisted_keys: set[str] = set()
            for r in contacts_to_save:
                ats_name, sep, slug = (r.source or "").partition("/")
                if sep and slug:
                    persisted_keys.add(f"{ats_name.strip().lower()}:{slug.strip().lower()}")
            explored_add = {f"{a}:{s}" for a, s, n, _t in probed if n == 0}
            explored_add |= {f"{a}:{s}" for a, s, n, _t in probed if n > 0} & persisted_keys
            record_explored_slugs(db, user.id, query_norm, explored_add)
        except Exception as e:
            db.rollback()
            log.debug(f"Hunt: cursor write skipped: {e}")
        # Persist board tags learned from this hunt's probes (bounded, so the
        # write burst can't eat the 52s budget on a first post-deploy hunt).
        try:
            written = 0
            for a, s, n, tags in probed:
                if tags and written < 20:
                    upsert_company_tags(db, a, s, tags)
                    written += 1
        except Exception as e:
            db.rollback()
            log.debug(f"Hunt: tag write skipped: {e}")

    # Signal for a useful empty state: how many leads we *found* (before resolution)
    # vs. how many resolved-but-were-already-saved. Lets the UI distinguish
    # "found hiring but no reachable email" from "all duplicates" from "nothing".
    # Exclude the synthetic "careers-inbox" entry: those are the app's OWN
    # per-domain careers@ probes derived from the scraped leads, not additional
    # hiring signals — counting them double-counts every company that produced one.
    found = sum(v for k, v in source_counts.items() if k != "careers-inbox")
    duplicates = max(0, len(contacts_to_save) - len(saved))

    return HuntResult(
        contacts=[{
            "id":           c.id,
            "name":         c.name,
            "email":        c.email,
            "designation":  c.designation,
            "company":      c.company,
            "status":       c.status,
            "confidence":   c.confidence,
            "email_status": c.email_status,
        } for c in saved],
        total=len(saved),
        found=found,
        duplicates=duplicates + skipped_known,
        role_filtered=role_filtered,
        duplicate_contacts=duplicate_contacts,
    )
