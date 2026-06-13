"""HackerNews 'Who is Hiring' scraper via free Algolia API."""

import re
from datetime import datetime, timedelta
import httpx
from app.scrapers.base import BaseScraper

EMAIL_RE  = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,7}\b')
COMPANY_RE = re.compile(r'^([A-Z][A-Za-z0-9\s&,.\-]{2,50}?)\s*[|│]')


class HackerNewsScraper(BaseScraper):
    name = "HackerNews"

    def __init__(self, lookback_days: int = 180):
        self.lookback_days = lookback_days

    async def search(self, query: str, **_) -> list[dict]:
        # Don't double-append "hiring" if already in query
        hn_query = query if "hiring" in query.lower() else f"{query} hiring"
        since = int((datetime.now() - timedelta(days=self.lookback_days)).timestamp())

        async with httpx.AsyncClient(
            timeout=20,
            headers={"User-Agent": "ColdReach/1.0 (contact finder; +https://github.com/yourname/coldreach)"},
        ) as client:
            resp = await client.get(
                "https://hn.algolia.com/api/v1/search",
                params={
                    "query": hn_query,
                    "tags": "comment",
                    "hitsPerPage": 50,
                    "numericFilters": f"created_at_i>{since}",
                    "attributesToHighlight": "none",
                },
            )
            resp.raise_for_status()

        contacts = []
        for hit in resp.json().get("hits", []):
            text = re.sub(r"<[^>]+>", " ", hit.get("comment_text", ""))
            text = re.sub(r"\s+", " ", text).strip()
            m = COMPANY_RE.match(text)
            company = m.group(1).strip() if m else "Unknown"

            # The hiring post itself is genuine context: what the company does and
            # who they want. Keep a trimmed snippet for the email generator.
            snippet = _post_snippet(text)

            for email in EMAIL_RE.findall(text):
                contacts.append({
                    "name":        hit.get("author", "HN Poster"),
                    "email":       email,
                    "company":     company,
                    "designation": "Founder / Hiring",
                    "source":      self.name,
                    "context":     f"From their 'Who is Hiring' post: {snippet}",
                })

        return contacts


def _post_snippet(text: str, limit: int = 600) -> str:
    """Trim a hiring post to a clean snippet, stripping the bare email address."""
    cleaned = EMAIL_RE.sub("", text).strip()
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rsplit(" ", 1)[0] + "…"
    return cleaned
