"""POST /api/hunt — runs all scrapers in parallel, resolves emails, saves results."""

import asyncio
import logging
import re
import time
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.crud import ContactRepository, add_known_company
from app.db.models import User
from app.deps import get_current_user
from app.schemas.contact import ContactCreate
from app.schemas.email import HuntRequest, HuntResult
from app.scrapers.base import is_valid_email, person_name_from_email, ROLE_LOCALS
from app.scrapers.hn import HackerNewsScraper, HNJobsScraper
from app.scrapers.github import GitHubScraper
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
from app.scrapers.web import emails_from_company_pages
from app.scrapers import directory
from app.scrapers.directory import looks_like_company
from app.scrapers.resolver import (
    resolve as resolver_resolve, ResolutionCache, mx_hosts, _smtp_probe,
)
from app.verifier import verify_email
from app.config import settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/hunt", tags=["hunt"])

# Upper bound on the email-resolution phase so hunts stay responsive even when
# outbound port 25 is blocked (every SMTP probe then burns its full timeout).
_RESOLVE_BUDGET_SECONDS = 45
_ROLE_ADDRESSES = ("talent", "recruiting", "careers", "jobs", "hr", "people", "team")

# Minimum confidence to persist a resolver-generated email. Direct scraper emails
# (confidence=0) are always kept; only resolver outputs are gated.
_MIN_RESOLVER_CONFIDENCE = 40

# Per-user hunt rate limit: prevent rapid repeated scraping that could get the
# server IP blocked by ATS APIs.
_HUNT_COOLDOWN_SECONDS = 15
_last_hunt: dict[int, float] = {}

# Alternative TLDs to try when the guessed .com domain has no MX.
# Ordered by how common they are for tech companies.
_ALT_TLDS = (".io", ".ai", ".co", ".app", ".dev", ".com")  # .com retried last as canonical fallback


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
    """Return sort key: 1 = Founder/CxO, 2 = HR/TA, 3 = Engineer, 4 = other."""
    d = designation.lower()
    if any(k in d for k in ("founder", "co-founder", "ceo", "cto", "chief", "founding")):
        return 1
    if any(k in d for k in ("hr", "human resource", "talent", "recruiter", "recruiting", "people ops", "people partner")):
        return 2
    if any(k in d for k in ("engineer", "developer", "swe", "software", "backend", "frontend", "fullstack", "devops", "data")):
        return 3
    return 4


def _build_scrapers(hunter_key: str) -> list:
    scrapers = [
        HackerNewsScraper(),
        HNJobsScraper(),
        GitHubScraper(),
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
               "_domain": None}
        if resolved.catch_all:
            out["email_status"] = "risky"   # deliverable but unprovable
        return out

    # No name → first try the company's own pages for a real, named person.
    page_emails = await emails_from_company_pages(domain)
    personal = _personal_email(page_emails, domain)
    if personal:
        return {**raw, "email": personal,
                "name": person_name_from_email(personal, raw.get("company") or ""),
                "designation": raw.get("designation") or "Team",
                "confidence": 50, "_domain": None}

    # Fall back to probing role mailboxes (hr@, talent@, …) — clearly labeled so
    # the user and the email generator know this reaches an inbox, not a person.
    mx = await cache.mx(domain)
    if not mx or await cache.catch_all(domain, mx):
        return None   # no MX, or catch-all makes role probing meaningless
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


@router.post("", response_model=HuntResult)
async def hunt(req: HuntRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Multi-source email hunt with pattern-resolution + SMTP verification.

    Flow:
    1. All scrapers run in parallel (HN, GitHub, Web, Greenhouse, Lever, Ashby, Hunter)
    2. Contacts with emails → validated immediately
    3. Contacts with _domain but no email → resolver pipeline (pattern learning + SMTP probe)
    4. All resolved contacts saved, sorted by designation priority
    """
    # Rate limit: one hunt per user per cooldown window.
    now = time.monotonic()
    last = _last_hunt.get(user.id, 0)
    if now - last < _HUNT_COOLDOWN_SECONDS:
        wait = int(_HUNT_COOLDOWN_SECONDS - (now - last)) + 1
        raise HTTPException(429, f"Please wait {wait}s before hunting again.")
    _last_hunt[user.id] = now
    if len(_last_hunt) > 512:   # prune expired entries so the map can't grow unbounded
        cutoff = now - _HUNT_COOLDOWN_SECONDS
        for uid in [u for u, t in _last_hunt.items() if t < cutoff]:
            _last_hunt.pop(uid, None)

    log.info(f"Hunt: {req.query!r}")
    scrapers = _build_scrapers(req.hunter_api_key)

    # Sources hit distinct hosts, so run them fully concurrently (no staggering).
    results_per_scraper = await asyncio.gather(
        *(s.safe_search(req.query) for s in scrapers)
    )

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

    for scraper, results in zip(scrapers, results_per_scraper):
        count = 0
        for r in results:
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

    log.info(f"Hunt: {len(with_email)} direct emails, {len(needs_resolve)} identity-only leads")

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

    # ── Resolve identity-only leads — concurrency-limited, with a time budget so
    #    a blocked port 25 can't make the hunt hang. ─────────────────────────────
    semaphore = asyncio.Semaphore(6)

    async def guarded_resolve(raw: dict) -> dict | None:
        async with semaphore:
            return await _resolve_domain_contact(raw, cache)

    tasks = [asyncio.create_task(guarded_resolve(r)) for r in needs_resolve[:40]]
    if tasks:
        done, pending = await asyncio.wait(tasks, timeout=_RESOLVE_BUDGET_SECONDS)
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
        # Honour an upstream "risky" set by the resolver (catch-all domain, role inbox)
        # regardless of what the cheap verifier returns — "unknown" is the common result
        # when there's no Hunter key, and must not silently overwrite "risky".
        r["email_status"] = "risky" if preset == "risky" else verdict
        verified.append(r)
    with_email = verified
    if dropped_invalid:
        log.info(f"Hunt: dropped {dropped_invalid} undeliverable addresses")

    # ── Sort: Founders → HR/TA → Engineers → rest; within each tier, verified
    #    emails before risky/unknown, then higher resolver confidence first, so
    #    the leads a user emails first are the ones most likely to land. ────────
    _status_rank = {"valid": 0, "unknown": 1, "risky": 2}
    with_email.sort(key=lambda r: (
        _desig_priority(r.get("designation") or ""),
        _status_rank.get(r.get("email_status") or "unknown", 1),
        -(r.get("confidence") or 0),
    ))

    # ── Persist ────────────────────────────────────────────────────────────────
    # "Unknown" company leaks straight into generated emails ("at Unknown") — when
    # the scraper couldn't name the company, derive it from the email's domain.
    repo = ContactRepository(db, user.id)
    contacts_to_save = [
        ContactCreate(
            name         = r.get("name") or "Contact",
            email        = r["email"],
            designation  = r.get("designation") or "Hiring Manager",
            company      = (
                (c if (c := (r.get("company") or "").strip()) and c != "Unknown" else "")
                or _company_from_email(r["email"]) or "Unknown"
            ),
            source       = r.get("source") or "",
            context      = r.get("context") or None,
            confidence   = r.get("confidence") or 0,
            email_status = r.get("email_status") or "unknown",
        )
        for r in with_email
    ]
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
            "source":       c.source,
            "status":       c.status,
            "confidence":   c.confidence,
            "email_status": c.email_status,
        } for c in saved],
        total=len(saved),
        sources=source_counts,
        found=found,
        duplicates=duplicates,
    )
