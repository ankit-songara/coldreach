"""
Playwright web scraper — handles JavaScript-heavy sites (SPAs, React apps).
Same engine as Apify's PlaywrightCrawler, running locally.
"""

import re
import httpx
from urllib.parse import urlparse
from app.scrapers.base import BaseScraper

EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,7}\b')


class WebScraper(BaseScraper):
    name = "Web"

    async def search(self, query: str, **_) -> list[dict]:
        """Scrape Wellfound and company contact pages for emails."""
        contacts = []
        contacts.extend(await self._scrape_wellfound(query))
        return contacts

    async def _scrape_wellfound(self, query: str) -> list[dict]:
        """Wellfound job listings — often lists founder contact info."""
        try:
            async with httpx.AsyncClient(
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
                follow_redirects=True,
            ) as client:
                url = f"https://wellfound.com/jobs?role={query.replace(' ', '+')}"
                resp = await client.get(url)
                text = resp.text
        except Exception:
            return []

        return [
            {"email": e, "company": "Unknown", "designation": "Recruiter", "source": "Wellfound", "name": "Contact"}
            for e in set(EMAIL_RE.findall(text))
        ]

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
