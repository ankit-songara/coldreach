"""
Company-page email harvesting.

`emails_from_company_pages(domain)` is the fast, dependency-free path used during
a hunt to turn a bare company domain into a real, named person's mailbox (from
/team, /about, /careers …) instead of a generic role inbox.
`find_published_role_email(domain)` grounds the P0 hiring-inbox lead in an
address the company actually publishes.
"""

import re
import asyncio
import httpx
from app.netguard import resolves_public

EMAIL_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')
_PAGES = ("/contact", "/about", "/team", "/careers", "/about-us", "/company")

# Image filenames and vendor domains that regex matches as "emails" — skip them.
_JUNK_RE = re.compile(
    r"@\d+x|@\d{2,}x\d{2,}|\.(png|jpe?g|gif|webp|svg|ico|woff2?|ttf|eot|css|js)$"
    r"|@(sentry|cloudflare|amazonaws|fonts\.gstatic|googleapis|example|test)\.",
    re.IGNORECASE,
)


def _clean(raw: list[str]) -> list[str]:
    out, seen = [], set()
    for e in raw:
        e = e.lower().strip().rstrip(".,;:")
        if e not in seen and not _JUNK_RE.search(e) and "@" in e and "." in e.split("@")[1]:
            seen.add(e)
            out.append(e)
    return out


# Standard hiring-inbox local parts, from real-world usage across startups,
# Indian IT firms, and global companies -- the same prefixes a blind guess
# picks from, but here used to RECOGNIZE a real one on the company's own page
# instead of inventing one.
HIRING_PREFIXES = frozenset({
    "careers", "career", "hr", "jobs", "hiring", "recruitment", "recruiting",
    "talent", "ta", "people",
})
# A published general inbox on the company's own site (contact@, hello@ …) is a
# REAL deliverable address — a much better P0 lead than a guessed careers@ that
# bounces. Excludes support@ (ticket systems) and sales@ (wrong audience).
GENERAL_PREFIXES = frozenset({"contact", "hello", "info", "mail", "office", "team", "admin"})
_ROLE_EMAIL_PAGES = ("/careers", "/jobs", "/contact", "/contact-us", "")
# Some corporate sites 403 obvious bot user-agents (observed live on
# controlf5.in) — this scanner needs a browser-like UA to see the same page a
# candidate would.
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


# ── Shared page-fetch cache ───────────────────────────────────────────────────
# A hunt schedules a company's careers-inbox lead (find_published_role_email)
# and its identity-only lead (emails_from_company_pages) at the SAME time, and
# both scan overlapping pages of the SAME domain — so without sharing, every
# company domain is fetched twice per hunt. This memoizes page text per-process
# and de-duplicates concurrent fetches of one URL, so the second lead reuses
# the first's fetches and repeat hunts are near-instant.
#
# Failure semantics ("" = miss): failures get a SHORT TTL — a transient blip
# must not poison a domain for the full hour — and a miss recorded under a
# shorter timeout never binds a caller willing to wait longer (the careers
# scan runs at 4s, the full page scan at 8s; a page that answers in 6s must
# stay reachable to the 8s scan, exactly as it was before this cache existed).
# Oversized bodies are served but not stored, bounding memory by bytes, not
# just entry count. Keyed on the full URL, deterministic given the shared
# browser UA + redirects.
_PAGE_TTL      = 3600       # pages we actually got
_PAGE_NEG_TTL  = 180        # failures: retryable in minutes, not an hour
_PAGE_MAX_BODY = 1_000_000  # don't hold multi-MB pages in memory for an hour
_page_cache: dict[str, tuple[float, str, float]] = {}   # url -> (stamp, text, timeout)
_page_inflight: dict[str, tuple["asyncio.Future[str]", float]] = {}


async def _cached_get(client: httpx.AsyncClient, url: str, timeout: float) -> str:
    import time
    hit = _page_cache.get(url)
    if hit is not None:
        stamp, text, fetched_with = hit
        ttl = _PAGE_TTL if text else _PAGE_NEG_TTL
        if time.monotonic() - stamp < ttl and (text or timeout <= fetched_with):
            return text

    entry = _page_inflight.get(url)
    if entry is not None:
        inflight, owner_timeout = entry
        # shield(): an un-shielded `await fut` makes the SHARED future this
        # task's cancellation target — cancelling one hunt's task would then
        # cancel the future out from under every OTHER hunt awaiting this URL.
        try:
            text = await asyncio.shield(inflight)
        except asyncio.CancelledError:
            if inflight.cancelled():
                text = ""   # the fetch itself died — treat as a miss
            else:
                raise       # WE were cancelled — propagate normally
        if text or timeout <= owner_timeout:
            return text
        # Owner gave up sooner than we would — fall through and fetch ourselves.

    loop = asyncio.get_running_loop()
    fut: "asyncio.Future[str]" = loop.create_future()
    _page_inflight[url] = (fut, timeout)
    try:
        try:
            resp = await client.get(url)
            text = resp.text if resp.is_success else ""
        except Exception:   # CancelledError is BaseException — deliberately not caught
            text = ""
        if len(_page_cache) > 4096:
            _page_cache.clear()
        if len(text) <= _PAGE_MAX_BODY:
            _page_cache[url] = (time.monotonic(), text, timeout)
        if not fut.done():
            fut.set_result(text)
        return text
    finally:
        _page_inflight.pop(url, None)
        if not fut.done():
            # Cancelled mid-fetch. NEVER fut.cancel() here: piggybackers can
            # belong to other, healthy hunts, and CancelledError sails past
            # their `except Exception` guards, killing scans that should have
            # survived. Hand them a miss instead — nothing is cached for this
            # URL, so it stays immediately retryable.
            fut.set_result("")


async def find_published_role_email(domain: str, timeout: int = 4) -> str | None:
    """
    Scan the highest-yield pages (/careers, /jobs, /contact, /contact-us,
    homepage) for an address the company actually PUBLISHES at its own domain:
    a hiring-inbox prefix first (careers@, hr@, jobs@, hiring@ …), else a
    general company inbox (contact@, hello@, info@ …).

    A published address is real evidence, not a guess -- this exists
    specifically so the P0 hiring-inbox lead in hunt.py is grounded whenever
    possible instead of blind-guessing "careers@domain" for every company
    (which bounces whenever the company actually uses a different local part).

    Pages are fetched CONCURRENTLY with a short per-request timeout, not the
    sequential 6-page emails_from_company_pages() scan -- this runs on the P0
    lead for EVERY company in a hunt and must fit many leads inside the shared
    resolve time budget (15s total on Vercel).
    """
    cached, value = _cache_get("pages", domain)
    if cached:
        return value

    if not await asyncio.to_thread(resolves_public, domain):
        return None

    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True,
            headers={"User-Agent": _BROWSER_UA},
        ) as client:
            texts = await asyncio.gather(
                *(_cached_get(client, f"https://{domain}{p}", timeout)
                  for p in _ROLE_EMAIL_PAGES)
            )
    except Exception:
        return None

    found: list[str] = []
    for text in texts:
        found.extend(EMAIL_RE.findall(text))
    cleaned = _clean(found)

    general: str | None = None
    result: str | None = None
    for email in cleaned:
        local, _, mail_domain = email.partition("@")
        if mail_domain != domain:
            continue
        if local in HIRING_PREFIXES:
            result = email
            break
        if general is None and local in GENERAL_PREFIXES:
            general = email
    result = result or general
    _cache_put("pages", domain, result)
    return result


# DDG blocks bursts — a small concurrency cap keeps a 30-lead hunt from
# tripping rate limits (individual failures degrade to None, never raise).
_WEB_SEARCH_SEM = asyncio.Semaphore(3)

# Per-domain result cache (hits AND misses) so repeat hunts and multi-source
# leads for the same company never re-scan or re-search within a process
# lifetime — matters doubly for DDG, which rate-limits repeated queries.
_GROUND_TTL = 6 * 3600
_ground_cache: dict[str, tuple[float, str | None]] = {}


def _cache_get(kind: str, domain: str) -> tuple[bool, str | None]:
    import time
    hit = _ground_cache.get(f"{kind}:{domain}")
    if hit and time.monotonic() - hit[0] < _GROUND_TTL:
        return True, hit[1]
    return False, None


def _cache_put(kind: str, domain: str, value: str | None) -> None:
    import time
    if len(_ground_cache) > 2048:
        _ground_cache.clear()
    _ground_cache[f"{kind}:{domain}"] = (time.monotonic(), value)


async def search_role_email_on_web(domain: str, company: str = "",
                                   timeout: int = 6) -> str | None:
    """
    Web-search grounding: query DuckDuckGo for the company's published
    careers/HR email and extract addresses at the target domain from the
    result snippets. Catches addresses published on third-party sites (job
    posts, directories, press pages) that the company's own site never
    renders server-side — live-verified to surface careers@talkcharge.com
    and hiring@astrotalk.com where the direct page scan finds nothing.

    Same trust bar as the page scan: only addresses actually seen in the
    wild, hiring prefixes first, then a general company inbox.
    """
    cached, value = _cache_get("search", domain)
    if cached:
        return value

    name = company.strip() or domain.rsplit(".", 1)[0].replace("-", " ").title()
    # Anchor the domain so it can't be a PREFIX of a longer one: without it,
    # 'acme.com' matched inside a published 'careers@acme.com.au' and we'd
    # persist a fabricated 'careers@acme.com' — a never-invent-emails violation.
    # The boundary blocks a REAL domain continuation (a label char, or a dot
    # FOLLOWED BY an alnum as in '.au'/'.community') but NOT a trailing prose
    # period ('...at careers@acme.com.'), which would otherwise drop genuine
    # sentence-final addresses from the DDG snippet text.
    domain_re = re.compile(
        r"[A-Za-z0-9._%+\-]+@" + re.escape(domain) + r"(?![A-Za-z0-9\-]|\.[A-Za-z0-9])",
        re.IGNORECASE,
    )
    async with _WEB_SEARCH_SEM:
        try:
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True,
                headers={"User-Agent": _BROWSER_UA},
            ) as client:
                resp = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": f"{name} careers email hr contact"},
                )
                # DDG signals rate-limiting with a 202 challenge page — a
                # transient condition that must NOT be cached as "no email
                # published for this domain".
                if resp.status_code != 200:
                    return None
                text = resp.text
        except Exception:
            return None

    general: str | None = None
    result: str | None = None
    for email in _clean(domain_re.findall(text)):
        local = email.split("@", 1)[0]
        if local in HIRING_PREFIXES:
            result = email
            break
        if general is None and local in GENERAL_PREFIXES:
            general = email
    result = result or general
    _cache_put("search", domain, result)
    return result


async def emails_from_company_pages(domain: str, timeout: int = 8) -> list[str]:
    """Scrape a company's public pages for email addresses.

    Primary: Scrapling StealthyFetcher + get_all_text() — extracts only visible
    text so it finds real emails (e.g. zeno@resend.com) instead of false-positive
    image filenames (favicon@57x57.png) that raw HTML regex returns.
    Also bypasses Cloudflare on many domains that httpx can't reach.

    Fallback: plain httpx for sites where Scrapling fails or isn't available.

    SSRF guard: refuses private/loopback/reserved domains.
    """
    if not await asyncio.to_thread(resolves_public, domain):
        return []

    emails = await _scrape_scrapling(domain, timeout)
    if emails:
        return emails
    return await _scrape_httpx(domain, timeout)


async def _scrape_scrapling(domain: str, timeout: int) -> list[str]:
    try:
        from scrapling.fetchers import StealthyFetcher
        fetcher = StealthyFetcher()
    except Exception:
        return []

    found: list[str] = []
    for path in _PAGES:
        try:
            page = await asyncio.wait_for(
                fetcher.async_fetch(f"https://{domain}{path}"),
                timeout=timeout,
            )
            text = page.get_all_text(ignore_tags=("script", "style", "noscript"))
            found.extend(EMAIL_RE.findall(text))
        except Exception:
            pass
        if len(found) >= 12:
            break
    return _clean(found)[:8]


async def _scrape_httpx(domain: str, timeout: int) -> list[str]:
    # Browser UA (not the bot UA) both dodges 403 walls and matches the UA
    # find_published_role_email uses, so the two share _cached_get entries for
    # the pages they scan in common (/careers, /contact).
    found: list[str] = []
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True,
            headers={"User-Agent": _BROWSER_UA},
        ) as client:
            for path in _PAGES:
                text = await _cached_get(client, f"https://{domain}{path}", timeout)
                if text:
                    found.extend(EMAIL_RE.findall(text))
                if len(found) >= 12:
                    break
    except Exception:
        pass
    return _clean(found)[:8]


