"""GitHub scraper — extracts developer emails from org commit history."""

import asyncio
import httpx
from app.scrapers.base import BaseScraper
from app.config import settings

GH_API  = "https://api.github.com"
NOREPLY = {"noreply.github.com", "users.noreply.github.com"}


def _headers() -> dict:
    h = {"Accept": "application/vnd.github.v3+json"}
    tok = (settings.github_token or "").strip()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


class GitHubScraper(BaseScraper):
    name = "GitHub"

    async def search(self, query: str, **_) -> list[dict]:
        """
        1. Find company GitHub orgs whose names match query terms
        2. Pull commit author emails from their public repos
        """
        contacts = []
        orgs = await self._find_orgs(query)
        tasks = [self._org_emails(org) for org in orgs[:3]]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, list):
                contacts.extend(result)
        return contacts

    async def _find_orgs(self, query: str) -> list[str]:
        # Extract 1-2 meaningful words from query for org search
        words = [w for w in query.lower().split() if len(w) > 2
                 and w not in {"hiring", "engineer", "developer", "senior", "lead"}]
        if not words:
            return []
        async with httpx.AsyncClient(headers=_headers(), timeout=15) as client:
            resp = await client.get(
                f"{GH_API}/search/users",
                params={"q": f"{words[0]} type:org", "per_page": 5},
            )
            if not resp.is_success:
                return []
            return [item["login"] for item in resp.json().get("items", [])]

    async def _org_emails(self, org: str) -> list[dict]:
        async with httpx.AsyncClient(headers=_headers(), timeout=20) as client:
            repos_r = await client.get(
                f"{GH_API}/orgs/{org}/repos",
                params={"per_page": 6, "sort": "updated", "type": "public"},
            )
            if not repos_r.is_success:
                return []

            seen: set[str] = set()
            contacts: list[dict] = []

            for repo in repos_r.json()[:5]:
                await asyncio.sleep(0.5)   # respect GitHub rate limits
                repo_name = repo["name"]
                repo_desc = (repo.get("description") or "").strip()
                repo_lang = repo.get("language") or ""
                commits_r = await client.get(
                    f"{GH_API}/repos/{org}/{repo_name}/commits",
                    params={"per_page": 20},
                )
                if not commits_r.is_success:
                    continue
                for c in commits_r.json():
                    a = c.get("commit", {}).get("author", {})
                    email = a.get("email", "")
                    name  = a.get("name", "")
                    if not email or email in seen:
                        continue
                    if any(skip in email for skip in NOREPLY):
                        continue
                    seen.add(email)
                    # Real signal: this person committed code to a specific repo.
                    ctx = f"Contributes to {org}/{repo_name}"
                    if repo_lang:
                        ctx += f" ({repo_lang})"
                    if repo_desc:
                        ctx += f" — {repo_desc[:160]}"
                    contacts.append({
                        "name":        name,
                        "email":       email,
                        "company":     org,
                        "designation": "Engineer",
                        "source":      f"{self.name}/{org}",
                        "context":     ctx,
                    })
            return contacts
