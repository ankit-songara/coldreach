"""Unit tests for scrapers."""

import pytest
from app.scrapers.base import is_valid_email, SKIP_EMAILS
from app.scrapers.hn import HackerNewsScraper
from app.scrapers.enricher import detect_email_pattern, apply_pattern
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
        result = asyncio.run(scraper.safe_search("__nonexistent_query_xyz_999__"))
        assert isinstance(result, list)


class TestHNSeeker:
    """The Ayaz incident: seeker posts must never be emitted as hiring leads."""

    def test_thread_classifier_hiring(self):
        from app.scrapers.hn import _is_hiring_thread
        assert _is_hiring_thread("Ask HN: Who is hiring? (June 2026)") is True

    def test_thread_classifier_seeker(self):
        from app.scrapers.hn import _is_hiring_thread
        assert _is_hiring_thread("Ask HN: Who wants to be hired? (June 2026)") is False
        assert _is_hiring_thread("Ask HN: Freelancer? (June 2026)") is False

    @pytest.mark.parametrize("post", [
        "Seeking work | Python dev | Remote",
        "I'm a backend engineer looking for new opportunities",
        "Available for freelance | Go | Distributed Systems",
        "SEEKING WORK | senior engineer | 5 yrs exp",
    ])
    def test_seeker_posts_are_filtered(self, post):
        from app.scrapers.hn import _SEEKER_POST_RE, _emit
        assert _SEEKER_POST_RE.match(post.lstrip()), f"Should match seeker: {post}"
        # _emit must return empty list for seeker posts
        fake_hit = {"author": "seeker@example.com"}
        # inject an email so the only filter keeping it out is the seeker guard
        assert _emit(fake_hit, post + " seeker@example.com", "HN") == []

    def test_hiring_post_is_not_filtered(self):
        from app.scrapers.hn import _emit
        post = "Acme Corp | We are hiring Python engineers | remote@acme.com"
        results = _emit({"author": "founder"}, post, "HN")
        assert len(results) == 1
        assert results[0]["email"] == "remote@acme.com"


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
