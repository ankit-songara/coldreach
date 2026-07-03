"""GitHub scraper — extracts developer emails from org commit history.

Org targeting is the make-or-break step:
  - company query ("Stripe")     → map via the directory domain + the literal name
  - role query   ("golang ...")  → find orgs behind repos *actively pushed* in that
                                    language, so we surface teams shipping right now
                                    instead of whatever org name happens to match a word.

Profile enrichment:
  - For the first 3 unique contributors per org we fetch their GitHub profile to get
    their real name, bio, and company — giving the LLM concrete context and the correct
    designation (Staff Engineer vs. plain Engineer).
"""

import re
import asyncio
from datetime import datetime, timezone, timedelta

import httpx
from app.scrapers.base import BaseScraper
from app.config import settings

GH_API  = "https://api.github.com"
NOREPLY = {"noreply.github.com", "users.noreply.github.com"}

# CI/automation authors — never real contacts.
# Exact-token hints are checked as whole words; substring hints (those ending with
# '@' or '[') are matched literally so 'bot' never falsely catches 'robotics.io'.
_BOT_EXACT = re.compile(
    r"\b(dependabot|renovate|greenkeeper|semantic-release|automation|snyk)\b",
    re.IGNORECASE,
)
_BOT_LITERAL = ("github-actions", "ci@", "actions@", "[bot]")


def _is_bot(name: str, email: str, login: str) -> bool:
    probe = f"{name} {email} {login}".lower()
    if any(lit in probe for lit in _BOT_LITERAL):
        return True
    return bool(_BOT_EXACT.search(probe))

# Query keyword → GitHub `language:` qualifier for repo search (role mode).
_LANG = {
    "go": "Go", "golang": "Go", "python": "Python", "rust": "Rust", "java": "Java",
    "javascript": "JavaScript", "js": "JavaScript", "node": "JavaScript",
    "nodejs": "JavaScript", "react": "JavaScript", "typescript": "TypeScript",
    "ts": "TypeScript", "ruby": "Ruby", "php": "PHP", "scala": "Scala",
    "kotlin": "Kotlin", "swift": "Swift", "cpp": "C++", "c++": "C++",
    "elixir": "Elixir", "clojure": "Clojure", "dart": "Dart",
}
_ORG_STOP = {
    "hiring", "engineer", "engineers", "developer", "developers", "dev", "senior",
    "junior", "lead", "staff", "principal", "remote", "fullstack", "backend",
    "frontend", "founding", "intern", "internship",
}

# Bio keywords → designation label (first match wins, checked in order)
_BIO_ROLES = [
    (("cto", "chief technology"), "CTO"),
    (("vp of eng", "vp engineering", "vp, eng"), "VP Engineering"),
    (("engineering manager", "eng manager"), "Engineering Manager"),
    (("tech lead", "technical lead"), "Tech Lead"),
    (("staff engineer",), "Staff Engineer"),
    (("principal engineer",), "Principal Engineer"),
    (("senior software", "senior engineer", "sr. engineer", "sr engineer"), "Senior Engineer"),
    (("software engineer", "swe", "backend engineer", "frontend engineer",
      "fullstack engineer", "full stack engineer"), "Software Engineer"),
    (("data engineer",), "Data Engineer"),
    (("ml engineer", "machine learning engineer"), "ML Engineer"),
    (("devops", "sre", "site reliability"), "DevOps / SRE"),
    (("mobile engineer", "ios engineer", "android engineer"), "Mobile Engineer"),
]


def _headers() -> dict:
    h = {"Accept": "application/vnd.github.v3+json"}
    tok = (settings.github_token or "").strip()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _has_token() -> bool:
    return bool((settings.github_token or "").strip())


def _designation_from_bio(bio: str) -> str:
    """Parse a GitHub bio string into the most specific designation we can infer."""
    if not bio:
        return "Engineer"
    b = bio.lower()
    for keywords, label in _BIO_ROLES:
        if any(k in b for k in keywords):
            return label
    return "Engineer"


def _build_context(org: str, repo_name: str, repo_lang: str, repo_desc: str,
                   bio: str, gh_company: str) -> str:
    """Build a rich context string from GitHub data for the email generator."""
    parts = [f"Contributes to {org}/{repo_name}"]
    if repo_lang:
        parts[0] += f" ({repo_lang})"
    if repo_desc:
        parts.append(f"Repo: {repo_desc[:160]}")
    if bio:
        parts.append(f"GitHub bio: {bio[:200]}")
    if gh_company and gh_company.lower().strip("@") not in (org.lower(), ""):
        parts.append(f"Listed company: {gh_company.strip('@')}")
    return ". ".join(parts)


class GitHubScraper(BaseScraper):
    name = "GitHub"

    async def search(self, query: str, **_) -> list[dict]:
        contacts = []
        orgs = await self._find_orgs(query)
        tasks = [self._org_emails(org) for org in orgs[:5]]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, list):
                contacts.extend(result)
        return contacts

    async def _find_orgs(self, query: str) -> list[str]:
        from app.scrapers.directory import lookup, looks_like_company

        async with httpx.AsyncClient(headers=_headers(), timeout=15) as client:
            # ── Company mode: target the named company directly ──────────────
            if looks_like_company(query):
                orgs: list[str] = []
                known = lookup(query)
                if known:
                    orgs.append(known.domain.split(".")[0])
                words = [re.sub(r"[^a-z0-9]", "", w) for w in query.lower().split()
                         if len(w) > 2 and w not in _ORG_STOP]
                orgs += words[:1]
                if not orgs:
                    resp = await client.get(f"{GH_API}/search/users",
                                            params={"q": f"{query} type:org", "per_page": 5})
                    if resp.is_success:
                        orgs = [i["login"] for i in resp.json().get("items", [])]
                return list(dict.fromkeys([o for o in orgs if o]))[:3]

            # ── Role mode: orgs behind repos actively pushed in this language ─
            lang = self._language_for(query)
            since = (datetime.now(timezone.utc) - timedelta(days=120)).strftime("%Y-%m-%d")
            q = f"language:{lang} pushed:>{since}" if lang else f"pushed:>{since} stars:>100"
            resp = await client.get(f"{GH_API}/search/repositories",
                                    params={"q": q, "sort": "updated", "order": "desc", "per_page": 30})
            if not resp.is_success:
                return []
            orgs = [
                (item.get("owner") or {}).get("login")
                for item in resp.json().get("items", [])
                if (item.get("owner") or {}).get("type") == "Organization"
            ]
            return list(dict.fromkeys([o for o in orgs if o]))[:5]

    @staticmethod
    def _language_for(query: str) -> str:
        for w in re.findall(r"[a-z0-9+#]+", query.lower()):
            if w in _LANG:
                return _LANG[w]
        return ""

    async def _org_emails(self, org: str) -> list[dict]:
        async with httpx.AsyncClient(headers=_headers(), timeout=20) as client:
            repos_r = await client.get(
                f"{GH_API}/orgs/{org}/repos",
                params={"per_page": 8, "sort": "updated", "type": "public"},
            )
            if not repos_r.is_success:
                return []

            seen_emails: set[str] = set()
            seen_logins: set[str] = set()
            contacts: list[dict] = []
            profiles_fetched = 0  # cap profile lookups per org to 3

            for repo in repos_r.json()[:7]:
                # Forks carry other projects' committers; archived repos carry
                # people who may have moved on years ago.
                if repo.get("fork") or repo.get("archived"):
                    continue
                await asyncio.sleep(0.3)
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
                    gh_login = (c.get("author") or {}).get("login", "")
                    if not email or email in seen_emails:
                        continue
                    if any(skip in email for skip in NOREPLY):
                        continue
                    if _is_bot(name, email, gh_login):
                        continue
                    seen_emails.add(email)
                    bio = ""
                    gh_company = org
                    designation = "Engineer"

                    # Profile enrichment: skip when no token is configured —
                    # unauthenticated GitHub is 60 req/hr shared by IP, and a
                    # single hunt already fires ~35 commit/repo calls. Adding
                    # profile lookups on top would exhaust the budget instantly.
                    if (gh_login and gh_login not in seen_logins
                            and profiles_fetched < 3 and _has_token()):
                        seen_logins.add(gh_login)
                        try:
                            p_r = await client.get(f"{GH_API}/users/{gh_login}",
                                                   headers=_headers())
                            if p_r.is_success:
                                p = p_r.json()
                                profiles_fetched += 1
                                real_name = (p.get("name") or "").strip()
                                if real_name:
                                    name = real_name
                                bio = (p.get("bio") or "").strip()
                                gh_company = (p.get("company") or org).strip()
                                designation = _designation_from_bio(bio)
                        except Exception:
                            pass

                    ctx = _build_context(org, repo_name, repo_lang, repo_desc, bio, gh_company)
                    contacts.append({
                        "name":        name,
                        "email":       email,
                        "company":     org,
                        "designation": designation,
                        "source":      f"{self.name}/{org}",
                        "context":     ctx,
                    })

            return contacts
