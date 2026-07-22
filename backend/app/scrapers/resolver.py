"""
Email pattern-resolution + SMTP verification engine.

Pipeline for a given (first, last, domain):
  1. MX lookup — confirm domain receives mail
  2. pattern learning — infer the domain's email format, keyless: from emails
     already observed this hunt, else from public GitHub commit authors
     (authenticated commit-search if a token is set, else an unauthenticated
     org-repo commit scan)
  3. detect_catch_all(domain, mx) — probe a random address; skip SMTP if catch-all
  4. smtp_rcpt_probe(email, mx) — RCPT TO check per candidate (non-destructive)
  5. confidence_score — weighted 0-100

No API key is required anywhere in this pipeline. On a serverless host where
outbound port 25 is blocked, SMTP verification is impossible, so a *guessed*
personal address stays low-confidence and is dropped by the hunt's floor — the
keyless way to surface a named person there is find_person_email() in web.py
(their address printed on the company's own pages), not this resolver.

Confidence bands:
  85-95  SMTP confirmed + pattern match
  50-60  SMTP confirmed, no pattern signal
  35-45  catch-all domain, pattern learned
  20-30  catch-all, no pattern (first.last guess)
  10-15  probes inconclusive, no signal
"""

import asyncio
import logging
import os
import random
import smtplib
import socket
import string
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import dns.resolver
import httpx

from app.config import settings
from app.netguard import resolves_public

log = logging.getLogger(__name__)

# Per-probe SMTP timeout. Kept modest so a host with port-25 blocked degrades
# quickly to pattern-only guessing instead of hanging on every candidate.
_SMTP_TIMEOUT = 6
_MAX_PROBES   = 3

GH_API  = "https://api.github.com"
_NOREPLY = frozenset({"noreply.github.com", "users.noreply.github.com", "github.com"})


# ── Data class ────────────────────────────────────────────────────────────────

@dataclass
class ResolvedEmail:
    email:      str
    confidence: int     # 0–100
    pattern:    str     # e.g. "first.last", "flast", "first"
    verified:   bool    # True = SMTP RCPT confirmed
    catch_all:  bool
    notes:      str = ""


# ── Permutation table ─────────────────────────────────────────────────────────

def _permutations(first: str, last: str, domain: str) -> list[tuple[str, str]]:
    """Return (email, pattern_name) in globally most-common order."""
    f, l  = first.lower().strip(), last.lower().strip()
    f1    = f[0] if f else ""
    l1    = l[0] if l else ""
    return [
        (f"{f}.{l}@{domain}",  "first.last"),
        (f"{f}@{domain}",       "first"),
        (f"{f1}{l}@{domain}",   "flast"),
        (f"{f1}.{l}@{domain}",  "f.last"),
        (f"{f}{l1}@{domain}",   "firstl") if l else None,
        (f"{l}@{domain}",       "last"),
        (f"{l}.{f}@{domain}",   "last.first"),
        (f"{f}-{l}@{domain}",   "first-last"),
    ]  # type: ignore[return-value]


def _permutations_clean(first: str, last: str, domain: str) -> list[tuple[str, str]]:
    return [(e, p) for pair in _permutations(first, last, domain)
            if pair is not None for e, p in [pair]]


def _email_for_pattern(first: str, last: str, domain: str, pattern: str) -> Optional[str]:
    for email, patt in _permutations_clean(first, last, domain):
        if patt == pattern:
            return email
    return None


def _classify_local(local: str, first: str, last: str) -> Optional[str]:
    """
    Given an email's local-part and the owner's name, return which pattern it is.
    Shared by GitHub commit learning and in-hunt observed-email learning.
    """
    local = local.lower()
    first, last = first.lower(), last.lower()
    f1 = first[0] if first else ""
    l1 = last[0] if last else ""
    if not first:
        return None
    if   last and local == f"{first}.{last}":  return "first.last"
    if   local == first:                        return "first"
    if   last and local == f"{f1}{last}":       return "flast"
    if   last and local == f"{f1}.{last}":      return "f.last"
    if   last and local == f"{first}{l1}":      return "firstl"
    if   last and local == last:                return "last"
    if   last and local == f"{last}.{first}":   return "last.first"
    if   last and local == f"{first}-{last}":   return "first-last"
    return None


def _infer_pattern(samples: list[tuple[str, str]]) -> Optional[str]:
    """Majority-vote a pattern from (email, full_name) pairs. Free, no network."""
    votes: dict[str, int] = defaultdict(int)
    for email, name in samples:
        if "@" not in email or not name or " " not in name:
            continue
        local = email.split("@", 1)[0]
        parts = name.strip().split()
        patt = _classify_local(local, parts[0], parts[-1])
        if patt:
            votes[patt] += 1
    return max(votes, key=votes.__getitem__) if votes else None


# ── DNS / MX ──────────────────────────────────────────────────────────────────

async def mx_hosts(domain: str) -> list[str]:
    """Return sorted MX hostnames, empty list on failure."""
    loop = asyncio.get_event_loop()
    try:
        records = await loop.run_in_executor(
            None,
            lambda: sorted(dns.resolver.resolve(domain, "MX"), key=lambda r: r.preference),
        )
        return [str(r.exchange).rstrip(".") for r in records]
    except Exception:
        return []


# ── SMTP RCPT probe ───────────────────────────────────────────────────────────

def _smtp_probe(email: str, mx_host: str, timeout: int = _SMTP_TIMEOUT) -> Optional[bool]:
    """
    Non-destructive RCPT TO probe.
    Returns True (accepted), False (rejected), None (inconclusive / policy).
    """
    # Serverless hosts (Vercel) block outbound port 25, so every probe would
    # just burn its full timeout. Bail out instantly — the resolver degrades
    # to pattern heuristics, which still produce usable guesses.
    if os.environ.get("VERCEL"):
        return None
    # SSRF guard: the MX host comes from a query-derived domain's DNS — never
    # connect to one that resolves to private/internal infrastructure.
    if not resolves_public(mx_host):
        return None
    try:
        with smtplib.SMTP(mx_host, 25, timeout=timeout) as smtp:
            smtp.ehlo("verify.local")
            code, _ = smtp.rcpt(email)
            return code in (250, 251)
    except smtplib.SMTPRecipientsRefused:
        return False
    except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError):
        return None
    except (socket.timeout, ConnectionRefusedError, OSError):
        return None
    except Exception:
        return None


async def detect_catch_all(domain: str, mx: list[str]) -> bool:
    """True if the domain accepts any address (SMTP probing pointless)."""
    if not mx:
        return False
    rand = "".join(random.choices(string.ascii_lowercase, k=14))
    probe = f"{rand}@{domain}"
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _smtp_probe, probe, mx[0])
    return result is True


# ── Pattern learning from GitHub ──────────────────────────────────────────────

async def learn_pattern(domain: str) -> Optional[str]:
    """
    Search GitHub commits for emails @domain and infer the company's email format.
    Returns the winning pattern name, or None if signal is insufficient.
    """
    headers: dict = {"Accept": "application/vnd.github.cloak-preview+json"}
    tok = (settings.github_token or "").strip()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    elif not tok:
        # Commit search requires auth — without a token it always 401s, so skip
        # the wasted round-trip. Cross-source learning still covers many domains.
        return None

    samples: list[tuple[str, str]] = []

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{GH_API}/search/commits",
                params={"q": f"author-email:{domain}", "per_page": 25},
                headers=headers,
            )
            if not resp.is_success:
                return None

            for item in resp.json().get("items", []):
                email = item.get("commit", {}).get("author", {}).get("email", "")
                if not email or "@" not in email:
                    continue
                if email.split("@", 1)[1].lower() != domain.lower():
                    continue
                if any(skip in email for skip in _NOREPLY):
                    continue

                login = (item.get("author") or {}).get("login", "")
                if not login:
                    continue
                user_r = await client.get(f"{GH_API}/users/{login}", headers=headers)
                if not user_r.is_success:
                    continue
                name = (user_r.json().get("name") or "").strip()
                if name and " " in name:
                    samples.append((email, name))
                if len(samples) >= 4:
                    break  # enough signal

        return _infer_pattern(samples)

    except Exception as exc:
        log.debug(f"learn_pattern({domain}): {exc}")
        return None


def _org_guess(domain: str) -> str:
    """Best-guess GitHub org slug from a domain — its registrable base label.
    'acme.com' -> 'acme', 'acme.co.uk' -> 'acme'. Empty if none usable."""
    labels = domain.lower().split(".")
    if len(labels) < 2:
        return ""
    base = labels[-2]
    if base in ("co", "com", "org", "net", "ac", "gov", "edu") and len(labels) >= 3:
        base = labels[-3]
    return base if len(base) > 1 else ""


# Unauthenticated GitHub REST is rate-limited to 60 req/hr per IP, so the keyless
# scan is bounded: one org-repo listing + a couple of repos' recent commits.
_KEYLESS_REPOS       = 2    # repos scanned per domain
_KEYLESS_COMMITS     = 30   # commits pulled per repo
_GH_KEYLESS_MAX_PER_HUNT = 4   # domains that may use the keyless scan per hunt


async def learn_pattern_keyless(domain: str) -> Optional[str]:
    """
    Token-free pattern learning: guess the company's GitHub org from its domain,
    read a couple of its public repos' recent commit authors, and infer the
    email format from any author addresses @domain. Uses the UNauthenticated
    REST API (no key), so it's best-effort and rate-limited — returns None on
    any miss. Complements the free in-hunt observed-email learning.
    """
    org = _org_guess(domain)
    if not org:
        return None
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "ColdReach/1.0"}
    samples: list[tuple[str, str]] = []
    try:
        async with httpx.AsyncClient(timeout=10, headers=headers) as client:
            repo_r = await client.get(
                f"{GH_API}/users/{org}/repos",
                params={"per_page": 6, "sort": "pushed", "type": "public"},
            )
            if not repo_r.is_success:
                return None
            repos = [r.get("name") for r in repo_r.json()
                     if isinstance(r, dict) and not r.get("fork") and r.get("name")]
            for repo in repos[:_KEYLESS_REPOS]:
                commit_r = await client.get(
                    f"{GH_API}/repos/{org}/{repo}/commits",
                    params={"per_page": _KEYLESS_COMMITS},
                )
                if not commit_r.is_success:
                    continue
                for item in commit_r.json():
                    author = ((item.get("commit") or {}).get("author") or {})
                    email = (author.get("email") or "").lower()
                    name = (author.get("name") or "").strip()
                    if ("@" in email and email.split("@", 1)[1] == domain.lower()
                            and not any(s in email for s in _NOREPLY)
                            and name and " " in name):
                        samples.append((email, name))
                if len(samples) >= 4:
                    break
        return _infer_pattern(samples)
    except Exception as exc:
        log.debug(f"learn_pattern_keyless({domain}): {exc}")
        return None


# ── Per-hunt resolution cache ───────────────────────────────────────────────────

class ResolutionCache:
    """
    Memoises domain-level facts across all contacts resolved in one hunt, so we
    never re-do MX / catch-all / pattern work for a shared domain.

    Crucially, observed real emails (from GitHub commits, HN posts, page scrapes,
    …) are pooled per domain and used to learn the company's pattern FOR FREE —
    no API call, no token. GitHub learning is only the fallback, and its keyless
    (unauthenticated) variant is budgeted per hunt against the 60-req/hr limit.
    """

    def __init__(self) -> None:
        self._mx:       dict[str, list[str]] = {}
        self._catchall: dict[str, bool]      = {}
        self._pattern:  dict[str, Optional[str]] = {}
        self._observed: dict[str, list[tuple[str, str]]] = defaultdict(list)
        # Keyless-GitHub budget: unauthenticated REST is 60 req/hr per IP, so
        # only a few domains per hunt may fall back to the org-repo commit scan.
        self._gh_keyless_calls = 0
        # Per-domain locks prevent concurrent tasks from double-firing catch_all
        # detection or learn_pattern for the same domain, which would waste API
        # calls and, for catch_all, produce non-deterministic results (two probes
        # with different random addresses can get opposite SMTP responses).
        # Keyed by (kind, domain) so mx/catch_all/pattern for one domain each
        # get their OWN lock: a single shared per-domain lock made the
        # gather(pattern, catch_all) in resolve() serialize (one holds the lock
        # across its whole body — GitHub or SMTP round-trip — while the other
        # blocks), defeating the intended parallelism. Distinct locks still
        # de-dup each fact kind (no double GitHub/SMTP work).
        self._locks:    dict[tuple[str, str], asyncio.Lock] = {}

    def _lock(self, domain: str, kind: str = "") -> asyncio.Lock:
        key = (kind, domain)
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def observe(self, email: str, name: str = "") -> None:
        """Record a real (email, name) seen this hunt, for pattern learning."""
        if email and "@" in email and name and " " in name.strip():
            self._observed[email.split("@", 1)[1].lower()].append((email, name))

    def seed_pattern(self, domain: str, pattern: str) -> None:
        """Preload a pattern learned in a PREVIOUS hunt (persisted in the DB) so
        this domain skips observation/GitHub learning entirely."""
        if domain and pattern:
            self._pattern[domain.lower()] = pattern

    def learned_patterns(self) -> dict[str, str]:
        """Every pattern this hunt actually resolved (seeded ones included) —
        harvested by the hunt route to persist for future hunts."""
        return {d: p for d, p in self._pattern.items() if p}

    async def mx(self, domain: str) -> list[str]:
        if domain not in self._mx:
            async with self._lock(domain, "mx"):
                if domain not in self._mx:
                    self._mx[domain] = await mx_hosts(domain)
        return self._mx[domain]

    async def catch_all(self, domain: str, mx: list[str]) -> bool:
        if domain not in self._catchall:
            async with self._lock(domain, "catchall"):
                if domain not in self._catchall:
                    self._catchall[domain] = await detect_catch_all(domain, mx)
        return self._catchall[domain]

    async def pattern(self, domain: str) -> Optional[str]:
        if domain in self._pattern:
            return self._pattern[domain]
        async with self._lock(domain, "pattern"):
            if domain in self._pattern:
                return self._pattern[domain]
            # 1. Free: infer from emails already discovered at this domain this hunt.
            result = _infer_pattern(self._observed.get(domain.lower(), []))
            # 2. GitHub commit SEARCH — precise but needs a token.
            if not result and (settings.github_token or "").strip():
                result = await learn_pattern(domain)
            # 3. Keyless fallback: unauthenticated org-repo commit scan, budgeted.
            if not result and self._gh_keyless_calls < _GH_KEYLESS_MAX_PER_HUNT:
                self._gh_keyless_calls += 1
                result = await learn_pattern_keyless(domain)
            self._pattern[domain] = result
        return result


# ── Master resolver ────────────────────────────────────────────────────────────

async def resolve(
    first: str, last: str, domain: str,
    cache: Optional[ResolutionCache] = None,
) -> Optional[ResolvedEmail]:
    """
    Full pipeline. Returns a ResolvedEmail with confidence score, or None if
    the domain has no MX records (not a real mail domain). Pass a ResolutionCache
    to share domain-level work across a hunt.
    """
    if not first or not last or not domain:
        return None

    cache = cache or ResolutionCache()

    # 1. MX
    mx = await cache.mx(domain)
    if not mx:
        log.debug(f"resolve({domain}): no MX — skip")
        return None

    # 2. Pattern learning + catch-all detection (parallel, cached)
    pattern, is_catch_all = await asyncio.gather(
        cache.pattern(domain),
        cache.catch_all(domain, mx),
    )
    log.debug(f"resolve({domain}): pattern={pattern} catch_all={is_catch_all}")

    # 3. Build ordered candidate list
    all_perms = _permutations_clean(first, last, domain)
    if pattern:
        ordered = [(e, p) for e, p in all_perms if p == pattern]
        ordered += [(e, p) for e, p in all_perms if p != pattern]
    else:
        ordered = all_perms

    # 4. Catch-all: skip probing, return best guess
    if is_catch_all:
        top_email, top_pattern = ordered[0]
        confidence = 40 if pattern else 20
        return ResolvedEmail(
            email=top_email, confidence=confidence,
            pattern=top_pattern, verified=False, catch_all=True,
            notes="catch-all domain — SMTP probe skipped",
        )

    # 5. SMTP probe (bounded candidates, stop on first confirmed)
    loop = asyncio.get_event_loop()
    for email, patt in ordered[:_MAX_PROBES]:
        result = await loop.run_in_executor(None, _smtp_probe, email, mx[0])
        if result is True:
            bonus = 35 if (pattern and patt == pattern) else 10
            return ResolvedEmail(
                email=email, confidence=min(50 + bonus, 95),
                pattern=patt, verified=True, catch_all=False,
            )
        elif result is False:
            continue
        else:
            break  # server policy — stop probing this domain

    # 6. Fallback: pattern-only guess
    if pattern:
        guess = _email_for_pattern(first, last, domain, pattern)
        if guess:
            return ResolvedEmail(
                email=guess, confidence=35, pattern=pattern,
                verified=False, catch_all=False,
                notes="SMTP inconclusive — pattern guess",
            )

    # 7. Last resort: first.last
    top_email, top_pattern = ordered[0]
    return ResolvedEmail(
        email=top_email, confidence=15, pattern=top_pattern,
        verified=False, catch_all=False,
        notes="no signal — first.last default",
    )
