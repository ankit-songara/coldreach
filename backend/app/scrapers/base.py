"""
Base scraper interface — Strategy pattern.

Every scraper implements BaseScraper. The hunt endpoint
composes multiple scrapers and runs them in parallel.

To add a new source:
  1. Create backend/app/scrapers/mysource.py
  2. Implement BaseScraper
  3. Register in app/api/hunt.py's _build_scrapers()
"""

from abc import ABC, abstractmethod
import logging
import re

log = logging.getLogger(__name__)

# Emails that should never appear in results (substring match anywhere)
SKIP_EMAILS: set[str] = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon",
    "bounce", "abuse", "spam", "postmaster",
}

# ── Junk detection: quality over quantity ─────────────────────────────────────
# Automated mailboxes nobody reads, test fixtures, and scrape artifacts. A cold
# email to notifications@ or test@ can never get a reply — worse, it pollutes
# the user's pipeline and makes every downstream count (drafted/sent/replied)
# lie. Exact local-part matches only, so real names that merely CONTAIN one of
# these ("Devika", "sysoev") are never hit.
#
# Deliberate role inboxes (talent@, careers@, jobs@, hr@, people@, team@,
# recruiting@) are NOT here — a real recruiter reads those at small startups.
# They stay, ranked last and labeled "risky · role inbox".
JUNK_LOCALS: frozenset[str] = frozenset({
    # machines talking to machines
    "automated", "automation", "auto", "notification", "notifications",
    "notify", "alert", "alerts", "update", "updates", "digest", "newsletter",
    "news", "reminder", "reminders", "calendar", "subscriptions", "subscribe",
    "unsubscribe", "webhook", "webhooks", "system", "robot", "bot", "daemon",
    "cron", "ci", "jenkins", "pipeline", "build", "deploy",
    # money/ops mailboxes
    "billing", "invoice", "invoices", "receipt", "receipts", "payment",
    "payments", "accounts", "accounting", "finance", "orders", "refunds",
    "security", "privacy", "legal", "compliance", "gdpr", "dmca",
    # marketing blasts
    "marketing", "promo", "promotions", "campaigns", "outreach",
    # test fixtures and dev debris
    "test", "testing", "tester", "test1", "test2", "testuser", "qa",
    "dev", "devnull", "staging", "sandbox", "demo", "example", "sample",
    "fake", "dummy", "asdf", "foo", "bar", "baz", "null", "void", "root",
    "localhost", "user", "username", "email", "someone", "anonymous",
})

# Reserved / test domains (exact) and suffixes that can never receive real mail.
JUNK_DOMAINS: frozenset[str] = frozenset({
    "example.com", "example.org", "example.net", "test.com", "localhost",
    "localhost.localdomain", "domain.com", "email.com", "yourcompany.com",
    "company.com", "mycompany.com", "yourdomain.com",
})
# RFC 2606/6761 reserved TLDs + file extensions that show up when a scraper's
# email regex bites into an asset path ("logo@2x.png" → domain "2x.png").
_JUNK_DOMAIN_SUFFIXES = (
    ".test", ".invalid", ".example", ".local", ".localhost", ".internal",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".css", ".js",
    ".woff", ".woff2", ".mp4", ".pdf",
)


def is_junk_email(email: str) -> bool:
    """True for automated mailboxes, test fixtures, and scrape artifacts."""
    e = email.lower().strip()
    if "@" not in e:
        return True
    local, domain = e.rsplit("@", 1)
    # "+tag" and dotted variants of a junk local are still junk
    # ("notifications+abc@", "no.reply@").
    base = local.split("+", 1)[0].replace(".", "").replace("-", "").replace("_", "")
    if local in JUNK_LOCALS or base in JUNK_LOCALS:
        return True
    if domain in JUNK_DOMAINS or domain.endswith(_JUNK_DOMAIN_SUFFIXES):
        return True
    return False


def is_valid_email(email: str) -> bool:
    e = email.lower().strip()
    if "@" not in e:
        return False
    local, domain = e.split("@", 1)
    return (
        len(local) > 0                   # local part must exist
        and "." in domain                # domain must have a dot
        and not any(s in e for s in SKIP_EMAILS)
        and not is_junk_email(e)
    )


# Role mailboxes — an email local part here is never a person's name.
ROLE_LOCALS = frozenset({
    "jobs", "careers", "career", "hiring", "hr", "talent", "recruiting", "recruit",
    "team", "info", "hello", "contact", "support", "admin", "work", "apply",
    "join", "joinus", "founders", "office", "mail", "people", "hey", "hi", "sales",
    "help", "press", "media", "partnerships", "general",
})


def person_name_from_email(email: str, company: str = "") -> str:
    """
    Derive a display name from an email local part IF it plausibly names a person
    ('sarah.chen@…' → 'Sarah Chen'). Role mailboxes, digit-bearing locals and
    company-named mailboxes yield '' — a greeting of 'Hi,' beats 'Hi Jobs,'.
    """
    local = email.split("@", 1)[0].lower()
    if local in ROLE_LOCALS or any(ch.isdigit() for ch in local):
        return ""
    parts = [p for p in re.split(r"[._\-+]+", local) if p.isalpha()]
    if not parts or len(parts) > 3 or any(p in ROLE_LOCALS for p in parts):
        return ""
    comp_norm = re.sub(r"[^a-z0-9]", "", (company or "").lower())
    if len(parts) == 1:
        tok = parts[0]
        # Single token: only accept short, non-company-like tokens ('sarah@acme.com').
        if len(tok) < 3 or len(tok) > 10:
            return ""
        if comp_norm and (tok in comp_norm or comp_norm in tok):
            return ""   # 'acme@acme.com' — a company mailbox, not a person
        return tok.title()
    return " ".join(p.title() for p in parts)


# ── Person-name plausibility ──────────────────────────────────────────────────
# Scrapers hand back whatever the source had: git author strings ("root",
# "ubuntu", "deploy bot"), org names ("Acme Careers"), test fixtures
# ("Test User", "John Doe"), or handles ("dev4life"). Persisting those as a
# person's name poisons everything downstream — the UI shows them, and worst,
# the email greeting becomes "Hi Careers," / "Hi Test,".

# Names that are placeholders, fixtures, or infrastructure — never a person.
TEST_IDENTITY_NAMES: frozenset[str] = frozenset({
    "test", "test user", "test account", "testing", "tester",
    "john doe", "jane doe", "john smith",
    "demo", "demo user", "example", "example user", "sample", "sample user",
    "asdf", "abc", "xyz", "aaa", "user", "admin", "administrator",
    "root", "ubuntu", "debian", "ec2-user", "deploy", "ci", "jenkins",
    "github actions", "dependabot", "renovate", "unknown", "n/a", "na",
    "none", "null", "name", "firstname lastname", "your name",
})

# Role/team words: a "name" containing one of these describes a function,
# not a person ("Hiring Team", "Acme Careers", "Lead Recruiter").
_ROLE_NAME_WORDS: frozenset[str] = frozenset({
    "team", "careers", "career", "hiring", "recruiting", "recruitment",
    "recruiter", "talent", "jobs", "notifications", "support", "office",
    "department", "dept", "staff", "hr", "info", "admin", "sales",
    "account", "accounts", "noreply", "mailer", "inbox", "help", "contact",
})

_NAME_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z'\-]*\.?$")


def is_test_identity(name: str) -> bool:
    """True when the name is a known placeholder/fixture — drop the lead."""
    return (name or "").strip().lower() in TEST_IDENTITY_NAMES


def plausible_person_name(name: str, company: str = "") -> bool:
    """
    Is this string plausibly a human's name (vs a handle, org, role, or fixture)?
    Deliberately conservative: a False here doesn't drop the lead, it only
    prevents the string from being greeted/displayed as a person.
    """
    n = (name or "").strip()
    if not n or len(n) > 60:
        return False
    low = n.lower()
    if low in TEST_IDENTITY_NAMES:
        return False
    if any(ch.isdigit() for ch in n) or "@" in n or "://" in n:
        return False   # handles ("dev4life"), emails, URLs
    tokens = n.split()
    if not 1 <= len(tokens) <= 4:
        return False
    for t in tokens:
        if not _NAME_TOKEN_RE.match(t):
            return False
        if t.lower().strip(".") in _ROLE_NAME_WORDS:
            return False   # "Hiring Team", "Acme Careers", "Lead Recruiter"
    # A name that IS the company isn't a person ("Vercel" / "Vercel Careers"
    # at company Vercel). Compare squashed lowercase forms both ways.
    comp = re.sub(r"[^a-z0-9]", "", (company or "").lower())
    if comp and len(comp) >= 3:
        name_sq = re.sub(r"[^a-z0-9]", "", low)
        first_sq = re.sub(r"[^a-z0-9]", "", tokens[0].lower())
        if name_sq == comp or first_sq == comp:
            return False
    return True


class BaseScraper(ABC):
    """All scrapers implement this interface."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable source name for logging and attribution."""
        ...

    @abstractmethod
    async def search(self, query: str, **kwargs) -> list[dict]:
        """
        Search for hiring contacts matching the query.

        Returns list of dicts with keys:
          name, email, company, designation, source
        """
        ...

    async def safe_search(self, query: str, **kwargs) -> list[dict]:
        """
        Wraps search() with error handling. Always returns a list.

        Keeps two kinds of result: those with a valid email, and identity-only
        leads carrying a `_domain` hint (resolved downstream in the hunt flow).
        """
        try:
            results = await self.search(query, **kwargs)
            valid = [
                r for r in results
                if is_valid_email(r.get("email", "")) or r.get("_domain")
            ]
            log.info(f"[{self.name}] {len(valid)} leads for '{query}'")
            return valid
        except Exception as exc:
            log.warning(f"[{self.name}] failed: {exc}")
            return []
