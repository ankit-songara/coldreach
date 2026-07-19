"""
Company-page email harvesting.

`emails_from_company_pages(domain)` is the fast, dependency-free path used during
a hunt to turn a bare company domain into a real, named person's mailbox (from
/team, /about, /careers …) instead of a generic role inbox. The Playwright-based
`WebScraper.scrape_company_contact_pages` remains for JS-rendered deep scrapes.
"""

import re
import asyncio
import httpx
from urllib.parse import urlparse
from app.scrapers.base import BaseScraper
from app.netguard import resolves_public

EMAIL_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')
_UA    = "ColdReach/1.0 (contact finder)"
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
    if not await asyncio.to_thread(resolves_public, domain):
        return None

    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True,
            headers={"User-Agent": _BROWSER_UA},
        ) as client:
            async def _fetch(path: str) -> str:
                try:
                    resp = await client.get(f"https://{domain}{path}")
                    return resp.text if resp.is_success else ""
                except Exception:
                    return ""

            texts = await asyncio.gather(*(_fetch(p) for p in _ROLE_EMAIL_PAGES))
    except Exception:
        return None

    found: list[str] = []
    for text in texts:
        found.extend(EMAIL_RE.findall(text))
    cleaned = _clean(found)

    general: str | None = None
    for email in cleaned:
        local, _, mail_domain = email.partition("@")
        if mail_domain != domain:
            continue
        if local in HIRING_PREFIXES:
            return email
        if general is None and local in GENERAL_PREFIXES:
            general = email
    return general


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
    found: list[str] = []
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True,
            headers={"User-Agent": _UA},
        ) as client:
            for path in _PAGES:
                try:
                    resp = await client.get(f"https://{domain}{path}")
                    if resp.is_success:
                        found.extend(EMAIL_RE.findall(resp.text))
                except Exception:
                    pass
                if len(found) >= 12:
                    break
    except Exception:
        pass
    return _clean(found)[:8]


class WebScraper(BaseScraper):
    name = "Web"

    async def search(self, query: str, **_) -> list[dict]:
        # Wellfound is a heavy SPA with no server-rendered emails — scraping it
        # over plain HTTP yielded nothing, so this source is intentionally inert.
        # Company-page harvesting now happens in the resolver via
        # emails_from_company_pages(). Kept registered for the Playwright path.
        return []

    async def scrape_company_contact_pages(self, domain: str) -> list[str]:
        """
        Visit a company's /contact /about /team /careers and collect emails.
        Uses Playwright for JS-rendered pages.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return await self._httpx_scrape(domain)

        emails: set[str] = set()
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                for path in ["/contact", "/about", "/team", "/careers", "/about-us"]:
                    try:
                        page = await browser.new_page()
                        await page.goto(f"https://{domain}{path}", timeout=15_000)
                        text = await page.inner_text("body")
                        await page.close()
                        for e in EMAIL_RE.findall(text):
                            emails.add(e)
                        if emails:
                            break
                    except Exception:
                        pass
                await browser.close()
        except Exception:
            return await self._httpx_scrape(domain)

        return list(emails)

    async def _httpx_scrape(self, domain: str) -> list[str]:
        """Lightweight fallback: plain HTTP scrape (no JS rendering)."""
        emails: set[str] = set()
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            for path in ["/contact", "/about", "/team"]:
                try:
                    resp = await client.get(f"https://{domain}{path}")
                    for e in EMAIL_RE.findall(resp.text):
                        emails.add(e)
                    if emails:
                        break
                except Exception:
                    pass
        return list(emails)


async def find_company_domain(company_name: str) -> str | None:
    """Guess company official domain via DuckDuckGo Instant Answer."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": company_name, "format": "json", "no_redirect": "1"},
            )
            data = resp.json()
            url = data.get("AbstractURL") or data.get("Redirect", "")
            if url:
                return urlparse(url).hostname.removeprefix("www.")  # type: ignore[union-attr]
    except Exception:
        pass
    return None
