"""Unit tests for scrapers."""

import pytest
from app.scrapers.base import is_valid_email, SKIP_EMAILS
from app.scrapers.hn import HackerNewsScraper
from app.scrapers.enricher import detect_email_pattern, apply_pattern


class TestEmailValidation:
    @pytest.mark.parametrize("email,expected", [
        ("ankit@razorpay.com",          True),
        ("founder@startup.io",          True),
        ("jobs@company.com",            True),  # valid for outreach
        ("hr@startup.com",              True),
        ("noreply@github.com",          False),
        ("mailer-daemon@server.com",    False),
        ("bounce@sendgrid.net",         False),
        ("spam@test.org",               False),
        ("not-an-email",                False),
        ("@nodomain.com",               False),
    ])
    def test_validation(self, email, expected):
        assert is_valid_email(email) == expected


class TestEmailPatternDetection:
    def test_firstname_lastname(self):
        pattern = detect_email_pattern(["ankit.rao@co.com", "priya.sharma@co.com"], "co.com")
        assert pattern == "firstname.lastname"

    def test_firstname_only(self):
        pattern = detect_email_pattern(["ankit@co.com", "priya@co.com"], "co.com")
        assert pattern == "firstname"

    def test_initial_lastname(self):
        pattern = detect_email_pattern(["a.rao@co.com"], "co.com")
        assert pattern == "f.lastname"

    def test_no_match_returns_none(self):
        pattern = detect_email_pattern(["other@other.com"], "co.com")
        assert pattern is None

    def test_apply_pattern(self):
        assert apply_pattern("Ankit", "Rao", "co.com", "firstname.lastname") == "ankit.rao@co.com"
        assert apply_pattern("Ankit", "Rao", "co.com", "firstname") == "ankit@co.com"
        assert apply_pattern("Ankit", "Rao", "co.com", "f.lastname") == "a.rao@co.com"


class TestHNScraper:
    def test_scraper_name(self):
        assert HackerNewsScraper().name == "HackerNews"

    def test_safe_search_returns_list_on_error(self):
        import asyncio
        scraper = HackerNewsScraper()
        # safe_search must never raise — returns empty list on any error
        result = asyncio.run(scraper.safe_search("__nonexistent_query_xyz_999__"))
        assert isinstance(result, list)
