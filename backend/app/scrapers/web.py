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
from urllib.parse import unquote, urlsplit
import httpx
from app.netguard import resolves_public

# curl_cffi impersonates a real Chrome at the TLS layer (JA3 fingerprint +
# headers), getting past anti-bot/Cloudflare walls that fingerprint the HANDSHAKE
# (not just the User-Agent) and 403 plain httpx despite a browser UA — observed
# live on e.g. coinbase. It's ~4MB with NO browser, so it fits the serverless
# function. Optional: if it isn't installed, page fetches fall back to httpx.
try:
    from curl_cffi.requests import AsyncSession as _CffiAsyncSession
except Exception:
    _CffiAsyncSession = None

EMAIL_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')
# Pages a real person's email is most likely printed on. Ordered by yield:
# contact/team/about first, then leadership/founder pages, then the well-known
# humans.txt (a convention that lists the team with contact info).
_PAGES = ("/contact", "/about", "/team", "/careers", "/about-us", "/company",
          "/our-team", "/people", "/leadership", "/founders", "/humans.txt")

# Anti-scrape obfuscation on "mailto"-shy sites: "jane [at] acme [dot] com",
# "jane(at)acme(dot)com", "jane {at} acme {dot} com". Only the BRACKETED /
# PARENTHESISED / BRACED forms are de-mangled — bare " at "/" dot " are ordinary
# English words and would corrupt prose, so they are deliberately NOT matched.
_AT_RE  = re.compile(r"\s*[\[({<]\s*(?:at|@)\s*[\])}>]\s*", re.IGNORECASE)
_DOT_RE = re.compile(r"\s*[\[({<]\s*(?:dot|\.)\s*[\])}>]\s*", re.IGNORECASE)


def _emails_in(text: str) -> list[str]:
    """All email addresses in a blob of page text, including bracket-obfuscated
    ones. De-mangling runs on a COPY so the plain-form matches are never lost."""
    if not text:
        return []
    out = EMAIL_RE.findall(text)
    if "[" in text or "(" in text or "{" in text or "<" in text:
        demangled = _DOT_RE.sub(".", _AT_RE.sub("@", text))
        out += EMAIL_RE.findall(demangled)
    return out

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


async def _final_host_public(requested_url: str, final_url) -> bool:
    """Redirect-chain guard: the pre-fetch resolves_public() check covers the
    ORIGINAL host only, so a redirect could bounce the request onto a private
    address. Re-check the LANDING host before trusting the body — but only
    when a redirect actually crossed hosts (the common no-redirect case costs
    nothing, and the DNS check runs off-thread). Unparseable URL → unsafe."""
    try:
        orig = urlsplit(requested_url).hostname or ""
        host = urlsplit(str(final_url)).hostname or ""
        if not host or host.lower() == orig.lower():
            return True     # same host the caller already vetted
        return await asyncio.to_thread(resolves_public, host)
    except Exception:
        return False


async def _fetch_text(client: httpx.AsyncClient, url: str, timeout: float) -> str:
    """GET a page as a real Chrome (curl_cffi TLS impersonation) to defeat
    anti-bot/Cloudflare walls, falling back to the passed httpx client if
    curl_cffi is unavailable or errors. Returns "" on non-2xx or failure.
    CancelledError propagates (mid-fetch cancel) — it's a BaseException, not
    caught here, so the caller's finally still runs.

    One SHARED deadline across both attempts: without it a hanging curl_cffi
    try burned its full timeout and THEN the httpx fallback burned the same
    again — every stuck page cost 2× its stated budget inside the resolve
    phase. The httpx fallback gets only whatever remains."""
    import time as _time
    start = _time.monotonic()
    if _CffiAsyncSession is not None:
        try:
            async with _CffiAsyncSession() as s:
                r = await s.get(url, impersonate="chrome", timeout=timeout,
                                allow_redirects=True)
            if 200 <= r.status_code < 300:
                return r.text if await _final_host_public(url, getattr(r, "url", url)) else ""
            return ""
        except Exception:
            pass   # transient/connection issue → try the httpx fallback
    remaining = timeout - (_time.monotonic() - start)
    if remaining <= 0.5:
        return ""   # curl_cffi consumed the budget — don't double-spend it
    try:
        r2 = await client.get(url, timeout=remaining)
        if r2.is_success:
            # getattr: response fakes in tests (and any transport without a
            # .url) count as same-host — i.e. already vetted, no re-check.
            return r2.text if await _final_host_public(url, getattr(r2, "url", url)) else ""
        return ""
    except Exception:
        return ""


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
    # Compare-and-set, not blind assignment: a piggybacker that fell through
    # (owner timed out sooner than it would) must not stomp a DIFFERENT task's
    # fresh registration — that overwrite silently defeated de-duplication for
    # everyone who arrived after it.
    cur = _page_inflight.get(url)
    if cur is None or (entry is not None and cur[0] is entry[0]):
        _page_inflight[url] = (fut, timeout)
    try:
        text = await _fetch_text(client, url, timeout)
        if len(_page_cache) > 4096:
            _page_cache.clear()
        if len(text) <= _PAGE_MAX_BODY:
            _page_cache[url] = (time.monotonic(), text, timeout)
        if not fut.done():
            fut.set_result(text)
        return text
    finally:
        # Pop only OUR registration — another task may own the slot now.
        if _page_inflight.get(url, (None,))[0] is fut:
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
        found.extend(_emails_in(text))
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
    # Cache a MISS only when at least one page actually returned content — an
    # all-failure scan (site briefly down, DNS blip, WAF hiccup) is a transient
    # outcome, and caching it as a definitive None silenced grounding for this
    # domain for the whole 6h TTL. A real "site up, nothing published" miss
    # still caches, which is the case the cache exists for.
    if result is not None or any(texts):
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


# ── Public LinkedIn profile discovery (keyless, never scrapes LinkedIn) ───────
# We only ever DISCOVER a public profile URL — from text we already fetched, or
# from a search engine's results. We never request linkedin.com, log in, or read
# profile content (that's against LinkedIn's ToS and gets blocked/banned).
_LINKEDIN_IN_RE = re.compile(r"linkedin\.com/in/([A-Za-z0-9._%\-]{2,100})", re.IGNORECASE)


def linkedin_urls_in(text: str) -> list[str]:
    """Every public LinkedIn /in/ profile URL in a blob of text, normalized and
    deduped. Handles percent-encoded forms (DDG wraps result links)."""
    out: list[str] = []
    seen: set[str] = set()
    for m in _LINKEDIN_IN_RE.finditer(unquote(text or "")):
        slug = m.group(1).rstrip("/").lower()
        if slug and slug != "in" and slug not in seen:
            seen.add(slug)
            out.append(f"https://www.linkedin.com/in/{slug}")
    return out


def linkedin_for_person(text: str, first: str, last: str) -> str | None:
    """The LinkedIn URL in `text` whose slug plausibly belongs to this person —
    LinkedIn slugs almost always contain the name (/in/jane-doe-1a2b)."""
    f, l = (first or "").lower(), (last or "").lower()
    for url in linkedin_urls_in(text):
        slug = url.rsplit("/", 1)[-1]
        if (f and f in slug) or (l and l in slug):
            return url
    return None


# Per-person LinkedIn cache (hits AND misses) so repeat hunts / multi-source
# leads never re-search the same name — DDG rate-limits repeated queries.
_LI_TTL = 6 * 3600
_li_cache: dict[str, tuple[float, str | None]] = {}


async def search_person_linkedin(first: str, last: str, company: str = "",
                                 timeout: int = 6) -> str | None:
    """DuckDuckGo lookup for a person's PUBLIC LinkedIn profile URL — reads the
    search results, never LinkedIn itself. Returns the name-matched /in/ URL or
    None. Cached per (name, company)."""
    import time
    if not first or not last:
        return None
    key = f"{first} {last} {company}".lower().strip()
    hit = _li_cache.get(key)
    if hit and time.monotonic() - hit[0] < _LI_TTL:
        return hit[1]

    query = f'"{first} {last}" {company} site:linkedin.com/in'.strip()
    async with _WEB_SEARCH_SEM:
        try:
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True,
                headers={"User-Agent": _BROWSER_UA},
            ) as client:
                resp = await client.get(
                    "https://html.duckduckgo.com/html/", params={"q": query})
                # 202 challenge = rate-limited; transient, don't cache as a miss.
                if resp.status_code != 200:
                    return None
                text = resp.text
        except Exception:
            return None

    result = linkedin_for_person(text, first, last)
    if len(_li_cache) > 2048:
        _li_cache.clear()
    _li_cache[key] = (time.monotonic(), result)
    return result


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


async def emails_from_company_pages(domain: str, timeout: int = 8,
                                    cap: int = 8) -> list[str]:
    """Scrape a company's public pages for email addresses.

    Primary: Scrapling StealthyFetcher + get_all_text() — extracts only visible
    text so it finds real emails (e.g. zeno@resend.com) instead of false-positive
    image filenames (favicon@57x57.png) that raw HTML regex returns.
    Also bypasses Cloudflare on many domains that httpx can't reach.

    Fallback: plain httpx for sites where Scrapling fails or isn't available.

    SSRF guard: refuses private/loopback/reserved domains.
    """
    return (await _company_pages(domain, timeout, cap))[0]


async def _company_pages(domain: str, timeout: int = 8,
                         cap: int = 8) -> tuple[list[str], str]:
    """Scrape once, return (emails, combined_visible_text). Scrapling first
    (renders JS, bypasses Cloudflare); httpx fallback when it yields nothing or
    isn't installed (the Vercel path). SSRF guard: private/reserved domains
    return empty."""
    if not await asyncio.to_thread(resolves_public, domain):
        return [], ""

    emails, text = await _scrape_scrapling(domain, timeout, cap)
    if emails:
        return emails, text
    return await _scrape_httpx(domain, timeout, cap)


def _ascii_fold(s: str) -> str:
    """'José' → 'jose', 'Søren' → 'soren'-ish: strip diacritics so a name can
    match its email local-part (mailboxes are ASCII even when names aren't).
    ø/æ/ß aren't decomposable combining forms — map the common ones directly."""
    import unicodedata
    s = (s or "").lower().strip()
    s = s.translate(str.maketrans({"ø": "o", "æ": "ae", "ß": "ss", "đ": "d", "ł": "l"}))
    return "".join(ch for ch in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(ch))


def _person_match_strength(local: str, first: str, last: str) -> int:
    """How strongly an email local-part identifies this person:
      2 = a both-token permutation (first.last, flast, first-last, …) — a
          collision needs two people sharing BOTH names at one company, so it's
          unambiguous and safe to trust on its own.
      1 = a single-token mailbox (bare `first` or bare `last`) — real for many
          small-company aliases, but `sarah@` could be a different Sarah, so the
          caller must corroborate it (see find_person_email).
      0 = no match.
    Accent-folded both sides so 'José García' matches jose.garcia@…."""
    local = _ascii_fold(local)
    f, l = _ascii_fold(first), _ascii_fold(last)
    if not f or not l:
        return 0
    f1, l1 = f[0], l[0]
    both = {
        f"{f}.{l}", f"{f}{l}", f"{f1}{l}", f"{f1}.{l}", f"{f}{l1}",
        f"{f}.{l1}", f"{f}-{l}", f"{f}_{l}", f"{l}.{f}", f"{l}{f}",
    }
    if local in both:
        return 2
    if local in {f, l}:
        return 1
    return 0


def _local_matches_person(local: str, first: str, last: str) -> bool:
    """Back-compat boolean: any recognized spelling (strength >= 1)."""
    return _person_match_strength(local, first, last) >= 1


async def find_person_email(domain: str, first: str, last: str,
                            timeout: int = 8) -> str | None:
    """
    Keyless personal-email grounding: scrape the company's own pages and return
    the address that belongs to THIS person (their name's standard permutation
    at the domain). A published address is real evidence — the resolver's
    pattern-guess needs SMTP/HTTP verification the serverless host can't do, but
    an email printed on the company's own /team page needs no verification at
    all. Returns None if no name-matched mailbox is published.

    A both-token match (first.last, flast, …) is trusted on its own. A bare
    single-token mailbox (sarah@ / chen@) is trusted ONLY when the lead's FULL
    name also appears on the scraped pages — otherwise it may belong to a
    different person who happens to share that one name. Legit leads whose bio
    is on the page still match (no lead lost); only the genuinely-ambiguous
    same-name case is rejected.
    """
    if not first or not last:
        return None
    # Wide cap: the generic 8-address cap could truncate THIS person's address
    # off a busy /team page before the name filter below ever saw it.
    emails, page_text = await _company_pages(domain, timeout, cap=64)
    domain = domain.lower()
    weak: str | None = None
    for e in emails:
        local, _, mail_domain = e.partition("@")
        if mail_domain != domain:
            continue
        strength = _person_match_strength(local, first, last)
        if strength >= 2:
            return e                      # unambiguous both-token match
        if strength == 1 and weak is None:
            weak = e                      # hold — corroborate against the page below
    if weak is None:
        return None
    folded = _ascii_fold(page_text)
    f, l = _ascii_fold(first), _ascii_fold(last)
    if f and l and f in folded and l in folded:
        return weak                       # full name is on the page → it's them
    return None


# ── Link-in-bio pages (Linktree & friends) ────────────────────────────────────
# People (esp. founders/creators) often put ONE bio-aggregator link in their
# profile that lists a direct, SELF-PUBLISHED email — an address a pattern-guess
# can't find (it may be a personal or a vanity domain). We only ever read a page
# whose host is on this allowlist, which also bounds the fetch to known public
# domains (a crafted provenance URL can't point us at an internal host → no SSRF).
_LINKBIO_HOSTS = (
    "linktr.ee", "linktree.com", "bio.link", "beacons.ai", "beacons.page",
    "stan.store", "carrd.co", "about.me", "lnk.bio", "linkin.bio", "solo.to",
    "tap.bio", "campsite.bio", "msha.ke", "hoo.be", "znap.link", "biolink.info",
)
_LINKBIO_RE = re.compile(
    r"https?://(?:www\.)?(?:"
    + "|".join(h.replace(".", r"\.") for h in _LINKBIO_HOSTS)
    + r")/[A-Za-z0-9._%\-/]+",
    re.IGNORECASE,
)


def linkbio_url_in(text: str) -> str | None:
    """The first link-in-bio (Linktree/bio.link/…) URL in a blob of text, or None.
    Percent-decoded first, since provenance notes often wrap URLs."""
    if not text:
        return None
    m = _LINKBIO_RE.search(unquote(text))
    return m.group(0).rstrip(".,);]'\"") if m else None


async def linkbio_email_for_person(text: str, first: str, last: str,
                                   timeout: int = 6) -> str | None:
    """If `text` links a person's bio-aggregator page, fetch that ONE page and
    return the email whose local part matches THIS person — a self-published
    address that needs no verification (like a printed one). Keyless, and only
    ever makes a request when such a URL is actually present. Returns None
    otherwise."""
    if not first or not last:
        return None
    url = linkbio_url_in(text)
    if not url:
        return None
    host = (urlsplit(url).hostname or "").lower()
    if not host or not await asyncio.to_thread(resolves_public, host):
        return None
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True,
            headers={"User-Agent": _BROWSER_UA},
        ) as client:
            page = await _cached_get(client, url, timeout)
    except Exception:
        return None
    for e in _clean(_emails_in(page)):
        if _local_matches_person(e.partition("@")[0], first, last):
            return e
    return None


async def _scrape_scrapling(domain: str, timeout: int, cap: int = 8) -> tuple[list[str], str]:
    """Returns (emails, combined_visible_text). The text is kept so callers can
    confirm a lead's full name is actually on the page (see find_person_email)."""
    try:
        from scrapling.fetchers import StealthyFetcher
        fetcher = StealthyFetcher()
    except Exception:
        return [], ""

    found: list[str] = []
    texts: list[str] = []
    for path in _PAGES:
        try:
            page = await asyncio.wait_for(
                fetcher.async_fetch(f"https://{domain}{path}"),
                timeout=timeout,
            )
            text = page.get_all_text(ignore_tags=("script", "style", "noscript"))
            texts.append(text)
            found.extend(_emails_in(text))
        except Exception:
            pass
        if len(found) >= max(12, cap + 4):
            break
    return _clean(found)[:cap], "\n".join(texts)


async def _scrape_httpx(domain: str, timeout: int, cap: int = 8) -> tuple[list[str], str]:
    # Browser UA (not the bot UA) both dodges 403 walls and matches the UA
    # find_published_role_email uses, so the two share _cached_get entries for
    # the pages they scan in common (/careers, /contact).
    #
    # Pages fetch CONCURRENTLY (semaphore 4): the sequential walk cost up to
    # len(_PAGES) × page-time inside the resolve budget — the single biggest
    # per-domain latency in a hunt. 4-wide keeps the per-domain burst polite
    # while cutting wall-clock ~4×; _cached_get still dedupes across callers.
    # Returns (emails, combined_visible_text).
    found: list[str] = []
    kept_texts: list[str] = []
    sem = asyncio.Semaphore(4)
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True,
            headers={"User-Agent": _BROWSER_UA},
        ) as client:
            async def one(path: str) -> str:
                async with sem:
                    return await _cached_get(client, f"https://{domain}{path}", timeout)
            texts = await asyncio.gather(*(one(p) for p in _PAGES),
                                         return_exceptions=True)
        for text in texts:
            if isinstance(text, str) and text:
                kept_texts.append(text)
                found.extend(_emails_in(text))
    except Exception:
        pass
    return _clean(found)[:cap], "\n".join(kept_texts)


