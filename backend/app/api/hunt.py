"""POST /api/hunt — runs all scrapers in parallel, resolves emails, saves results."""

import asyncio
import logging
import os
import re
import time
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.crud import (
    ContactRepository, add_known_company,
    get_domain_patterns, record_domain_pattern,
)
from app.db.models import User
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
from app.scrapers.jobboards import (
    RemoteOKScraper, RemotiveScraper, ArbeitnowScraper,
    JobicyScraper, HimalayasScraper, TheMuseScraper, WeWorkRemotelyScraper,
)
from app.scrapers.web import (
    emails_from_company_pages, find_published_role_email,
    search_role_email_on_web, HIRING_PREFIXES,
)
from app.scrapers import directory
from app.scrapers.directory import looks_like_company
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
# "careers@" and "jobs@" are the most universally standard convention across
# company sizes and countries — tried first. "talent@"/"hr@" skew larger/tech-
# forward; "people@"/"team@" skew startup-specific and are the least reliable.
_ROLE_ADDRESSES = ("careers", "jobs", "hiring", "hr", "talent", "recruiting", "recruitment", "people", "team")

# Minimum confidence to persist a resolver-generated email. Direct scraper emails
# (confidence=0) are always kept; only resolver outputs are gated.
_MIN_RESOLVER_CONFIDENCE = 40

# Cap on P0 careers-inbox leads per hunt (one per unique company domain).
_MAX_CAREERS_LEADS = 30

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
        RemoteOKScraper(),
        RemotiveScraper(),
        ArbeitnowScraper(),
        JobicyScraper(),
        HimalayasScraper(),
        TheMuseScraper(),
        WeWorkRemotelyScraper(),
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
    for tld in _ALT_TLDS:
        candidate = base + tld
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
        if published:
            prefix = published.split("@", 1)[0]
            # A dedicated hiring inbox beats a general company inbox, but both
            # are REAL published addresses — either way this lead can't be the
            # kind of blind guess that bounces.
            if prefix in HIRING_PREFIXES:
                desig, conf = "Talent/Recruiting (role inbox)", 70
            else:
                desig, conf = "Company Inbox (role inbox)", 60
            return {**raw, "email": published, "name": prefix.title(),
                    "designation": desig,
                    "confidence": conf, "email_status": "valid", "_domain": None}

        if settings.hunter_api_key:
            generic = await HunterEnricher(settings.hunter_api_key).search_generic(domain)
            if generic:
                prefix = generic.split("@", 1)[0]
                return {**raw, "email": generic, "name": prefix.title(),
                        "designation": "Talent/Recruiting (role inbox)",
                        "confidence": 65, "email_status": "valid", "_domain": None}
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


# ── Live "who's hiring" suggestions ──────────────────────────────────────────
# Company names with active engineering postings right now, for the Hunt page's
# suggestion chips — clicking one runs a company hunt directly. Cached
# module-level so the chips cost one RemoteOK fetch per process per TTL, not
# one per page load.
_SUGGEST_TTL_SECONDS = 900
# "at" starts at -inf, NOT 0.0: time.monotonic() is time-since-boot, so on a
# freshly booted host (or a cold-started serverless microVM) 0.0 would read as
# "fetched < TTL ago" and the first request would serve [] without fetching.
_suggest_cache: dict = {"at": float("-inf"), "companies": []}

# RemoteOK company names arrive as filed: "LOTHIAN BUSES LIMITED", "Acme Pvt.
# Ltd." — legalese and shouting make the suggestion chips look like junk data.
_LEGAL_SUFFIX_RE = re.compile(
    r"[\s,]+(private\s+limited|pvt\.?\s*ltd\.?|limited|ltd\.?|llc|inc\.?|corp\.?|gmbh)\s*$",
    re.IGNORECASE,
)


def _display_company(raw: str) -> str:
    name = " ".join(raw.split())
    name = _LEGAL_SUFFIX_RE.sub("", name).strip(" .,|-")
    if name.isupper() and len(name) > 4:   # keep real acronyms (IBM, SAP) intact
        name = name.title()
    return name


@router.get("/suggestions")
async def hunt_suggestions(user: User = Depends(get_current_user)):
    now = time.monotonic()
    if now - _suggest_cache["at"] > _SUGGEST_TTL_SECONDS:
        companies: list[str] = []
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(
                timeout=8, follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as client:
                r = await client.get("https://remoteok.com/api")
                if r.is_success:
                    seen: set[str] = set()
                    for j in r.json():
                        if not isinstance(j, dict):
                            continue
                        comp = _display_company(j.get("company") or "")
                        pos  = (j.get("position") or "").lower()
                        tags = " ".join(t.lower() for t in (j.get("tags") or []) if isinstance(t, str))
                        hay  = f"{pos} {tags}"
                        # Tech postings only (position OR tags), plausible company names.
                        if (comp and comp.lower() not in seen and 2 < len(comp) <= 30
                                and any(k in hay for k in (
                                    "engineer", "developer", "sde", "devops", "backend",
                                    "frontend", "full", "software", "sre", "platform",
                                    "data", "mobile", "ios", "android", "golang", "python",
                                    "react", "node", "java", "rust",
                                ))):
                            seen.add(comp.lower())
                            companies.append(comp)
                        if len(companies) >= 12:
                            break
        except Exception as e:
            log.debug(f"Suggestions: RemoteOK fetch failed: {e}")
        # Serve stale data over nothing if the refresh failed.
        if companies:
            _suggest_cache.update(at=now, companies=companies)
        else:
            _suggest_cache["at"] = now - _SUGGEST_TTL_SECONDS + 60  # retry in 1 min
    return {"hiring_companies": _suggest_cache["companies"]}


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
    scrapers = _build_scrapers(req.hunter_api_key)

    # Sources hit distinct hosts, so run them fully concurrently (no
    # staggering) — but bounded: one slow board must not eat the wall-clock
    # budget the resolve phase needs. Completed scrapers are harvested; the
    # stragglers are cancelled and count as empty.
    scrape_tasks = [asyncio.create_task(s.safe_search(req.query)) for s in scrapers]
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

    # ── Split: known-email contacts vs identity-only (need resolution) ─────────
    seen_emails: set[str] = set()
    with_email:  list[dict] = []
    needs_resolve: list[dict] = []
    source_counts: dict[str, int] = {}

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
                    with_email.append({**r, "email": email, "confidence": r.get("confidence", 0)})
                    count += 1
            elif r.get("_domain"):
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
    domain_company: dict[str, str] = {}
    for r in needs_resolve:
        d = (r.get("_domain") or "").lower().strip()
        if d and d not in _FREEMAIL and d not in domain_company:
            domain_company[d] = (r.get("company") or "").strip()
    for r in with_email:
        d = r["email"].rsplit("@", 1)[-1]
        if d and d not in _FREEMAIL and d not in domain_company:
            domain_company[d] = (r.get("company") or "").strip()
    if looks_like_company(req.query):
        guess = _guess_company_domain(req.query)
        if guess and guess not in domain_company:
            domain_company[guess] = req.query.strip()

    careers_leads = [
        {"name": "", "company": comp or _company_from_email(f"x@{dom}"),
         "designation": "", "source": "careers-inbox", "_domain": dom}
        for dom, comp in list(domain_company.items())[:_MAX_CAREERS_LEADS]
    ]
    if careers_leads:
        source_counts["careers-inbox"] = len(careers_leads)

    log.info(f"Hunt: {len(with_email)} direct emails, {len(careers_leads)} P0 careers leads, "
             f"{len(needs_resolve)} identity-only leads")

    # ── Seed a shared cache with every real email found, so cross-source pattern
    #    learning works for free (e.g. GitHub emails at acme.com teach acme.com's
    #    pattern, applied to an ATS recruiter lead at the same domain). ──────────
    cache = ResolutionCache()
    for r in with_email:
        cache.observe(r["email"], r.get("name") or "")

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
            if not cur_named and new_named:
                # Incoming has a name — adopt it, merge context from the old entry
                merged_ctx = " ".join(
                    filter(None, [r.get("context"), cur.get("context")])
                )[:2000]
                by_domain[d] = {**r, "context": merged_ctx or r.get("context")}
            else:
                # Keep existing name; supplement context if the new entry has more
                existing_ctx = (cur.get("context") or "")
                extra_ctx    = (r.get("context") or "")
                if extra_ctx and extra_ctx not in existing_ctx:
                    merged_ctx = (existing_ctx + "\n" + extra_ctx).strip()[:2000]
                    by_domain[d] = {**cur, "context": merged_ctx}
    needs_resolve = sorted(
        by_domain.values(),
        key=lambda r: 0 if " " in (r.get("name") or "").strip() else 1,
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
    tasks = [asyncio.create_task(guarded_resolve(r))
             for r in careers_leads + needs_resolve[:40]]
    if tasks:
        # Adaptive: whatever wall-clock the scrape phase consumed comes out of
        # the resolve budget so the total stays inside the serverless limit.
        resolve_budget = max(
            _MIN_RESOLVE_SECONDS,
            min(_RESOLVE_BUDGET_SECONDS,
                _TOTAL_HUNT_BUDGET_SECONDS - (time.monotonic() - hunt_t0)),
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
    verdicts = await asyncio.gather(
        *(loop.run_in_executor(None, verify_email, e) for e in (r["email"] for r in with_email))
    )

    verified: list[dict] = []
    dropped_invalid = 0
    for r, verdict in zip(with_email, verdicts):
        if verdict == "invalid":
            dropped_invalid += 1
            continue
        preset = r.get("email_status")
        # Honour an upstream "risky" or "valid" set by the resolver (catch-all
        # domain / unverified guess = risky; a grounded, actually-published
        # address = valid) regardless of what the cheap verifier returns —
        # "unknown" is the common result when there's no Hunter key, and must
        # not silently overwrite either.
        r["email_status"] = preset if preset in ("risky", "valid") else verdict
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

    repo = ContactRepository(db, user.id)
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
    saved = repo.bulk_create(contacts_to_save)
    log.info(f"Hunt complete: {len(saved)} new contacts saved")

    # Signal for a useful empty state: how many leads we *found* (before resolution)
    # vs. how many resolved-but-were-already-saved. Lets the UI distinguish
    # "found hiring but no reachable email" from "all duplicates" from "nothing".
    found = sum(source_counts.values())
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
        duplicates=duplicates,
        role_filtered=role_filtered,
    )
