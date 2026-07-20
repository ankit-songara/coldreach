"""
Company → ATS directory + query classification helpers.

Maps a company to which ATS hosts its public job board (greenhouse | lever |
ashby | smartrecruiters | recruitee), the board slug, and its real email domain.

The directory is a MERGED view from two sources, exposed behind `lookup()` and
`companies_for_ats()` so callers never care where an entry came from:

  1. companies.csv  — curated seed, version-controlled. Add a row, no code change.
  2. runtime registry — companies added at startup from the `known_companies` DB
     table (user-added in the UI + auto-discovered from company-name hunts) via
     `register()`. This lets the directory grow without editing code or redeploying.

Wrong entries are harmless — a bad slug just 404s and is skipped.

Two query modes drive the ATS scrapers:
  - company query ("Amazon")        → derive slug(s), hit boards directly
  - role query    ("golang hiring") → scan directory, filter jobs by role title
"""

import re
import csv
import logging
from pathlib import Path
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Company:
    name:   str
    slug:   str
    ats:    str     # greenhouse | lever | ashby | smartrecruiters | recruitee
    domain: str


_CSV_PATH = Path(__file__).with_name("companies.csv")


def _load_seed() -> list[Company]:
    """Load the curated company seed from companies.csv (next to this module)."""
    out: list[Company] = []
    try:
        with _CSV_PATH.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                name   = (row.get("name") or "").strip()
                slug   = (row.get("slug") or "").strip()
                ats    = (row.get("ats") or "").strip().lower()
                domain = (row.get("domain") or "").strip().lower()
                if name and slug and ats:
                    out.append(Company(name, slug, ats, domain))
    except FileNotFoundError:
        log.warning("companies.csv not found at %s — directory seed is empty", _CSV_PATH)
    return out


# Curated seed (read-only) + runtime registry (DB-loaded, user-added, discovered).
_SEED: list[Company] = _load_seed()
_RUNTIME: dict[tuple[str, str], Company] = {}


def _key(ats: str, slug: str) -> tuple[str, str]:
    return (ats.lower().strip(), slug.lower().strip())


def all_companies() -> list[Company]:
    """Merged directory: CSV seed plus anything registered at runtime."""
    merged = {_key(c.ats, c.slug): c for c in _SEED}
    merged.update(_RUNTIME)     # runtime extends (never silently shadows the seed)
    return list(merged.values())


def register(name: str, slug: str, ats: str, domain: str = "") -> bool:
    """
    Add a company to the live directory. Returns True if it was newly added.
    Idempotent — re-registering an existing (ats, slug) is a no-op. This is how
    the DB-backed and hunt-discovered companies enter the in-memory directory.
    """
    name, slug, ats = name.strip(), slug.strip(), ats.strip().lower()
    if not (name and slug and ats):
        return False
    k = _key(ats, slug)
    if k in _RUNTIME or any(_key(c.ats, c.slug) == k for c in _SEED):
        return False
    _RUNTIME[k] = Company(name, slug, ats, (domain or "").strip().lower())
    return True


def unregister(ats: str, slug: str) -> bool:
    """Remove a runtime-registered company (seed entries can't be removed)."""
    return _RUNTIME.pop(_key(ats, slug), None) is not None


def is_known(ats: str, slug: str) -> bool:
    """True if this (ats, slug) is already in the seed or runtime directory."""
    k = _key(ats, slug)
    return k in _RUNTIME or any(_key(c.ats, c.slug) == k for c in _SEED)


# ── Lookups ───────────────────────────────────────────────────────────────────

def companies_for_ats(ats: str) -> list[Company]:
    """Directory companies hosted on a given ATS (used in role-query mode)."""
    return [c for c in all_companies() if c.ats == ats]


def lookup(name: str) -> Company | None:
    """Exact-ish directory match for a company-name query."""
    norm = _norm(name)
    for c in all_companies():
        if _norm(c.name) == norm or c.slug == norm:
            return c
    return None


def slugify_company(name: str) -> list[str]:
    """
    Candidate board slugs to try for an unknown company-name query.
    e.g. "Acme Corp" → ["acmecorp", "acme"]. Only slugs that 200 survive.
    """
    base = _norm(name)
    cands = [base]
    words = re.findall(r"[a-z0-9]+", name.lower())
    if words:
        cands.append(words[0])              # first word alone
        cands.append("".join(words[:2]))    # first two joined
    # de-dupe, preserve order, drop empties
    seen, out = set(), []
    for s in cands:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


# ── Query classification ───────────────────────────────────────────────────────

_ROLE_WORDS = {
    "hiring", "engineer", "engineers", "developer", "developers", "dev",
    "backend", "frontend", "fullstack", "full-stack", "senior", "junior",
    "lead", "staff", "principal", "react", "golang", "go", "python", "java",
    "rust", "node", "nodejs", "typescript", "devops", "sre", "data", "ml",
    "ai", "manager", "designer", "intern", "internship", "remote", "sales",
    "marketing", "product", "qa", "android", "ios", "mobile", "platform",
    "infrastructure", "security", "growth", "founding",
}
_STOP = {"at", "for", "the", "a", "an", "in", "of", "and", "jobs", "careers", "role", "roles"}


def looks_like_company(query: str) -> bool:
    """
    Heuristic: a short query with no role/keyword words is a company name.
    "Amazon" / "Stripe Inc" → True.  "golang hiring" / "react engineer" → False.
    """
    words = [w for w in re.findall(r"[a-z0-9\-]+", query.lower()) if w not in _STOP]
    if not words or len(words) > 3:
        return False
    return not any(w in _ROLE_WORDS for w in words)


def company_matches(query: str, company: str) -> bool:
    """
    True only if `company` is actually the company named in `query` — word-aware,
    not a loose substring. "visa" matches "Visa" / "Visa Inc" but NOT "Provisa",
    and crucially NOT a job post that merely mentions "visa sponsorship".
    """
    q = _norm(query)
    if not q:
        return False
    if q == _norm(company):
        return True
    # Every query WORD must appear as a whole token in the company name, so a
    # multi-word hunt matches a longer legal name: "Goldman Sachs" → "Goldman
    # Sachs Group", "New York Times" → "The New York Times". Still word-aware
    # (whole tokens, not substrings), so "visa"≠"Provisa" and "stripe"≠"Striped".
    qtokens = re.findall(r"[a-z0-9]+", query.lower())
    ctokens = set(re.findall(r"[a-z0-9]+", (company or "").lower()))
    return bool(qtokens) and set(qtokens) <= ctokens


def role_keywords(query: str) -> list[str]:
    """Meaningful role tokens for filtering job titles (role-query mode)."""
    return [
        w for w in re.findall(r"[a-z0-9\-]+", query.lower())
        if w not in _STOP and w != "hiring" and len(w) > 1
    ]


# Tokens that name a specific technology/specialisation. When a query contains
# one, a listing must match IT — not just a generic word like "engineer", or
# "react engineer" would match every engineering job on every board.
_TECH_TOKENS = {
    "go", "golang", "python", "rust", "java", "javascript", "js", "node", "nodejs",
    "react", "typescript", "ts", "ruby", "rails", "php", "scala", "kotlin", "swift",
    "elixir", "clojure", "dart", "c++", "cpp", "django", "fastapi", "flask", "spring",
    "vue", "angular", "svelte", "nextjs", "kubernetes", "k8s", "docker", "terraform",
    "aws", "gcp", "azure", "devops", "sre", "ml", "ai", "data", "android", "ios",
    "mobile", "flutter", "graphql", "postgres", "postgresql", "kafka", "spark", "blockchain",
    "security", "embedded", "qa", "frontend", "backend", "fullstack",
}


# Interchangeable spellings: a "golang" query must match a listing that says "Go".
_TECH_ALIASES: dict[str, set[str]] = {
    "go":         {"go", "golang"},
    "golang":     {"golang", "go"},
    "js":         {"js", "javascript"},
    "javascript": {"javascript", "js"},
    "ts":         {"ts", "typescript"},
    "typescript": {"typescript", "ts"},
    "k8s":        {"k8s", "kubernetes"},
    "kubernetes": {"kubernetes", "k8s"},
    "cpp":        {"cpp", "c++"},
    "c++":        {"c++", "cpp"},
    "node":       {"node", "nodejs"},
    "nodejs":     {"nodejs", "node"},
    "postgres":   {"postgres", "postgresql"},
    "postgresql": {"postgresql", "postgres"},
    "ml":         {"ml", "machine learning"},
    "ai":         {"ai", "artificial intelligence", "llm"},
}


def _tech_in(token: str, hay: str) -> bool:
    """Word-boundary match so 'go' ≠ 'governance' and 'java' ≠ 'javascript'."""
    pattern = r"\b" + re.escape(token)
    if token[-1].isalnum():
        pattern += r"\b"
    return re.search(pattern, hay) is not None


def role_match(query: str, haystack: str) -> bool:
    """
    True if a job title/tags/text matches the role query.
    Tech-aware: if the query names a technology, the listing must mention that
    technology (or an alias of it); generic tokens ("engineer", "senior") alone
    don't qualify a listing.
    """
    kws = role_keywords(query)
    if not kws:
        return True
    hay = haystack.lower()
    tech = [k for k in kws if k in _TECH_TOKENS]
    if tech:
        variants: set[str] = set()
        for t in tech:
            variants |= _TECH_ALIASES.get(t, {t})
        return any(_tech_in(v, hay) for v in variants)
    return any(k in hay for k in kws)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())
