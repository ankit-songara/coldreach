"""Unit tests for scrapers."""

import pytest
from app.scrapers.base import is_valid_email, SKIP_EMAILS
from app.scrapers.directory import looks_like_company, company_matches


class TestQueryClassification:
    @pytest.mark.parametrize("query,expected", [
        ("visa", True), ("Stripe", True), ("Acme Inc", True), ("Bosch Group", True),
        ("golang hiring", False), ("react engineer", False), ("python backend", False),
        ("data engineer india", False),
    ])
    def test_looks_like_company(self, query, expected):
        assert looks_like_company(query) is expected

    @pytest.mark.parametrize("query,company,expected", [
        ("visa", "Visa", True),
        ("visa", "Visa Inc", True),
        ("visa", "Provisa", False),          # substring, not a word → no match
        ("stripe", "Stripe", True),
        ("stripe", "Striped", False),
        ("bosch", "Bosch Group", True),
        ("acme", "Unknown", False),          # unparsed company never matches
    ])
    def test_company_matches_is_word_aware(self, query, company, expected):
        # This is the guard that stopped "visa" matching every "visa sponsorship" post.
        assert company_matches(query, company) is expected


class TestEmailValidation:
    @pytest.mark.parametrize("email,expected", [
        ("ankit@razorpay.com",          True),
        ("founder@startup.io",          True),
        ("jobs@stripe.com",             True),  # role inbox — valid for outreach
        ("hr@startup.com",              True),
        ("jobs@company.com",            False),  # company.com is a placeholder junk domain
        ("noreply@github.com",          False),
        ("mailer-daemon@server.com",    False),
        ("bounce@sendgrid.net",         False),
        ("spam@test.org",               False),
        ("not-an-email",                False),
        ("@nodomain.com",               False),
    ])
    def test_validation(self, email, expected):
        assert is_valid_email(email) == expected


class TestEmailPageScraper:
    """email_from_company_pages junk filter must strip false-positives."""

    def test_junk_filter_removes_image_filenames(self):
        from app.scrapers.web import _clean
        raw = ["favicon@57x57.png", "icon@2x.jpg", "zeno@resend.com", "logo@3x.png"]
        result = _clean(raw)
        assert result == ["zeno@resend.com"]

    def test_junk_filter_removes_vendor_domains(self):
        from app.scrapers.web import _clean
        raw = ["err@sentry.com", "cdn@cloudflare.com", "real@startup.io"]
        result = _clean(raw)
        assert "real@startup.io" in result
        assert all("sentry" not in e and "cloudflare" not in e for e in result)


class TestSiblingVariants:
    def test_backend_expands_to_language_tokens(self):
        from app.scrapers.directory import sibling_variants, _TECH_TOKENS
        v = sibling_variants("backend engineer hiring")
        assert "golang" in v and "python" in v
        # Every variant must be a single tech token — a non-tech variant would
        # drop role_match into its generic branch and match every "engineer".
        assert all(t in _TECH_TOKENS for t in v)

    def test_no_tech_token_means_no_expansion(self):
        from app.scrapers.directory import sibling_variants
        assert sibling_variants("founding engineer") == []
        assert sibling_variants("Stripe") == []

    def test_alias_of_primary_not_offered_as_sibling(self):
        from app.scrapers.directory import sibling_variants
        v = sibling_variants("golang hiring")
        assert "go" not in v and "golang" not in v


class TestBoardSiblingTagging:
    def test_sibling_listing_tagged_primary_untagged(self, monkeypatch):
        import asyncio
        import httpx
        from app.scrapers.jobboards import RemoteOKScraper

        listings = [
            {"company": "PrimaryCo", "position": "Backend Engineer",
             "tags": [], "description": "", "apply_url": "https://primaryco.io/jobs"},
            {"company": "SiblingCo", "position": "Golang Engineer",
             "tags": [], "description": "", "apply_url": "https://siblingco.io/jobs"},
            {"company": "NoMatchCo", "position": "Account Executive",
             "tags": [], "description": "", "apply_url": "https://nomatchco.io/jobs"},
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[{"legal": "notice"}] + listings)

        real_client = httpx.AsyncClient
        monkeypatch.setattr(httpx, "AsyncClient",
                            lambda *a, **kw: real_client(transport=httpx.MockTransport(handler)))

        leads = asyncio.run(RemoteOKScraper().search(
            "backend engineer hiring", query_variants=("golang", "python")))
        by_co = {l["company"]: l for l in leads}
        assert "PrimaryCo" in by_co and not by_co["PrimaryCo"].get("_sibling")
        assert "SiblingCo" in by_co and by_co["SiblingCo"].get("_sibling") is True
        assert "NoMatchCo" not in by_co

    def test_no_variants_means_no_sibling_matches(self, monkeypatch):
        import asyncio
        import httpx
        from app.scrapers.jobboards import RemoteOKScraper

        listings = [{"company": "SiblingCo", "position": "Golang Engineer",
                     "tags": [], "description": "", "apply_url": "https://siblingco.io/x"}]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[{"legal": "n"}] + listings)

        real_client = httpx.AsyncClient
        monkeypatch.setattr(httpx, "AsyncClient",
                            lambda *a, **kw: real_client(transport=httpx.MockTransport(handler)))
        leads = asyncio.run(RemoteOKScraper().search("backend engineer hiring"))
        assert leads == []


class TestAtsCursorTargeting:
    def _fake_directory(self, monkeypatch, slugs):
        from types import SimpleNamespace
        import app.scrapers.ats as ats_mod
        monkeypatch.setattr(
            ats_mod, "companies_for_ats",
            lambda key: [SimpleNamespace(slug=s, domain=f"{s}.com") for s in slugs],
        )

    def test_explored_slugs_excluded(self, monkeypatch):
        from app.scrapers.ats import GreenhouseScraper
        self._fake_directory(monkeypatch, ["alpha", "beta", "gamma", "delta"])
        s = GreenhouseScraper()
        targets = s._targets("backend hiring", company_mode=False,
                             explored_slugs=frozenset({"greenhouse:alpha", "greenhouse:beta"}))
        assert {t[0] for t in targets} == {"gamma", "delta"}

    def test_wraparound_when_pool_exhausted(self, monkeypatch):
        from app.scrapers.ats import GreenhouseScraper
        self._fake_directory(monkeypatch, ["alpha", "beta"])
        s = GreenhouseScraper()
        targets = s._targets("backend hiring", company_mode=False,
                             explored_slugs=frozenset({"greenhouse:alpha", "greenhouse:beta"}))
        # Everything explored → full pool again, never an empty scan.
        assert {t[0] for t in targets} == {"alpha", "beta"}

    def test_other_ats_cursor_keys_ignored(self, monkeypatch):
        from app.scrapers.ats import GreenhouseScraper
        self._fake_directory(monkeypatch, ["alpha", "beta"])
        s = GreenhouseScraper()
        targets = s._targets("backend hiring", company_mode=False,
                             explored_slugs=frozenset({"lever:alpha"}))
        assert {t[0] for t in targets} == {"alpha", "beta"}


class TestHNPressDomainsRejected:
    """A hiring post linking its funding coverage must not make the PRESS
    site the company domain — that grounded a journalist's published email
    as a 'recruiter' (observed live: connie@techcrunch.com)."""

    def test_press_url_skipped_company_url_kept(self):
        from app.scrapers.hackernews import _domain_from_text
        text = ("Acme | Senior Go Engineer | Remote. We just raised our Series A "
                "(https://techcrunch.com/2026/07/acme-raises) — join us! "
                "More at https://acme.dev/careers")
        assert _domain_from_text(text) == "acme.dev"

    def test_press_only_post_yields_no_domain(self):
        from app.scrapers.hackernews import _domain_from_text
        text = "Beta | Rust Engineer | see https://www.forbes.com/beta-profile"
        assert _domain_from_text(text) == ""
