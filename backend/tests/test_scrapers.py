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
