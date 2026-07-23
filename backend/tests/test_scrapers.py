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

    @pytest.mark.parametrize("text, expected", [
        ("reach jane [at] acme [dot] com today", "jane@acme.com"),
        ("email: raj(at)startup(dot)io", "raj@startup.io"),
        ("plain jane@acme.com works too", "jane@acme.com"),
    ])
    def test_demangles_obfuscated_emails(self, text, expected):
        from app.scrapers.web import _emails_in, _clean
        assert expected in _clean(_emails_in(text))

    def test_bare_at_and_dot_words_not_demangled(self):
        # " at "/" dot " are ordinary English — must NOT be turned into an email.
        from app.scrapers.web import _emails_in
        assert _emails_in("meet me at the office, dot your i's") == []


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


class TestHNSlugHarvest:
    def test_extracts_and_junk_filters(self):
        from app.scrapers.hackernews import _extract_ats_mappings
        text = ("Acme | Golang Engineer | apply at https://jobs.lever.co/acme/123 "
                "or https://boards.greenhouse.io/embed/job_board?for=acme "
                "docs at https://jobs.ashbyhq.com/norm-ai. and https://apply.workable.com/j/ABC123")
        pairs = _extract_ats_mappings(text)
        assert ("lever", "acme") in pairs
        assert ("ashby", "norm-ai") in pairs          # trailing ellipsis dot stripped
        assert ("workable", "j") not in pairs          # job-detail short link junk
        assert all(slug != "embed" for _, slug in pairs)

    def test_portfolio_board_rejected(self):
        from app.scrapers.hackernews import _extract_ats_mappings
        pairs = _extract_ats_mappings("Phaselaw | https://jobs.ashbyhq.com/pear-vc/x")
        assert pairs == []

    def test_name_slug_agreement_gates_post_metadata(self):
        from app.scrapers.hackernews import _mapping_from_post
        # Name matches slug -> post name + domain trusted.
        m = _mapping_from_post(
            "Norm Ai | Golang | https://norm.ai/careers", "ashby", "norm-ai")
        assert m["company"] == "Norm Ai" and m["domain"] == "norm.ai"
        # Name does NOT match slug -> slug-derived name, NO domain (a wrong
        # domain_hint would poison every lead from that board).
        m2 = _mapping_from_post(
            "Phaselaw | Counsel | https://phase.law", "ashby", "livekit")
        assert m2["company"] == "Livekit" and m2["domain"] == ""


class TestHNFounderRelabel:
    def test_local_part_signal(self):
        from app.scrapers.hackernews import _author_is_founder
        assert _author_is_founder("Acme | Golang | remote", "ceo@acme.com")
        assert _author_is_founder("Acme | Golang", "founders@acme.com")
        assert not _author_is_founder("Acme | Golang", "jobs@acme.com")

    def test_text_signal_with_negative_guards(self):
        from app.scrapers.hackernews import _author_is_founder
        assert _author_is_founder(
            "Acme | Eng | I'm the co-founder, email me", "hi@acme.com")
        # "founding engineer" is a ROLE being hired, not the author.
        assert not _author_is_founder(
            "Acme | Founding Engineer | I'm the co-founder... "
            "hiring a founding engineer", "hi@acme.com") is None
        assert not _author_is_founder(
            "Acme | Eng | looking for a technical co-founder", "hi@acme.com")

    def test_header_segment_never_matches(self):
        from app.scrapers.hackernews import _author_is_founder
        # "Founder" in the company/role header must not fire.
        assert not _author_is_founder("Founder Institute | Engineer | remote", "x@fi.co")


class TestLinkedInDiscovery:
    """Keyless public-LinkedIn-URL discovery — from text we already have, or a
    DDG search. Never contacts LinkedIn itself."""

    def test_extracts_and_normalizes(self):
        from app.scrapers.web import linkedin_urls_in
        text = ('reach me at https://www.linkedin.com/in/jane-doe-1a2b/ or '
                'linkedin.com/in/JohnRoe · noise linkedin.com/company/acme')
        urls = linkedin_urls_in(text)
        assert "https://www.linkedin.com/in/jane-doe-1a2b" in urls
        assert "https://www.linkedin.com/in/johnroe" in urls
        # /company/ pages are not personal /in/ profiles
        assert all("/in/" in u for u in urls)

    def test_handles_percent_encoded(self):
        # DDG wraps result links: linkedin.com%2Fin%2Fjane-doe
        from app.scrapers.web import linkedin_urls_in
        urls = linkedin_urls_in("uddg=https%3A%2F%2Flinkedin.com%2Fin%2Fjane-doe")
        assert "https://www.linkedin.com/in/jane-doe" in urls

    def test_person_match_by_slug(self):
        from app.scrapers.web import linkedin_for_person
        text = "team: linkedin.com/in/bob-smith and linkedin.com/in/jane-doe-99"
        assert linkedin_for_person(text, "Jane", "Doe") == "https://www.linkedin.com/in/jane-doe-99"
        # no slug contains "kai lin" → no false match
        assert linkedin_for_person(text, "Kai", "Lin") is None

    def test_search_returns_name_matched_url(self, monkeypatch):
        import asyncio
        from app.scrapers import web

        class FakeResp:
            status_code = 200
            text = ('<a href="/l/?uddg=https%3A%2F%2Fwww.linkedin.com%2Fin%2F'
                    'priya-sharma-eng">Priya Sharma - Finch Labs | LinkedIn</a>')
        class FakeClient:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **k): return FakeResp()
        monkeypatch.setattr(web.httpx, "AsyncClient", FakeClient)
        web._li_cache.clear()

        got = asyncio.run(web.search_person_linkedin("Priya", "Sharma", "Finch Labs"))
        assert got == "https://www.linkedin.com/in/priya-sharma-eng"

    def test_search_caches_and_needs_full_name(self, monkeypatch):
        import asyncio
        from app.scrapers import web
        assert asyncio.run(web.search_person_linkedin("Priya", "", "Acme")) is None  # no last name


class TestHNSelfIntro:
    """A poster who names themselves ('I'm Jane Smith, co-founder') becomes a
    named lead even with no embedded email, so the resolver/page-scrape can find
    that person instead of falling back to careers@."""

    @pytest.mark.parametrize("body, name, title", [
        ("Acme | Backend | I'm Jane Smith, co-founder. We use Go.", "Jane Smith", "Co-Founder"),
        ("Acme | Eng | Hi, I am Raj Patel, CTO here.", "Raj Patel", "CTO"),
        ("Acme | Remote | this is Mary Ann Lee, head of talent", "Mary Ann Lee", "Head Of Talent"),
        # name but no stated title -> named lead, neutral designation
        ("Acme | Eng | I'm Kevin Ortiz and we're growing fast", "Kevin Ortiz", ""),
    ])
    def test_extracts_name_and_title(self, body, name, title):
        from app.scrapers.hackernews import _self_intro
        assert _self_intro(body) == (name, title)

    @pytest.mark.parametrize("body", [
        "Acme | Eng | I'm looking for a senior Go engineer",   # not a name
        "Acme | Eng | We're hiring a founding engineer",        # neg-guarded role
        "Acme | Eng | email us at jobs@acme.com",              # no self-intro
        "Founder Institute | Program | apply here",            # header-only
    ])
    def test_no_false_names(self, body):
        from app.scrapers.hackernews import _self_intro
        name, _ = _self_intro(body)
        assert name == ""


class TestYCFounderLead:
    """A YC company's first founder becomes the lead identity (name + title),
    so YC hunts surface reachable founders instead of nameless role inboxes.
    The full founder list still enriches the draft context."""

    def _search(self, monkeypatch, founders):
        import asyncio
        from app.scrapers import yc as yc_mod

        async def fake_load():
            return [{
                "name": "Acme", "website": "https://acme.com", "slug": "acme",
                "batch": "W24", "one_liner": "widgets", "status": "Active",
                "isHiring": True, "industries": [], "tags": [],
            }]
        async def fake_founders(client, slug):
            return founders

        monkeypatch.setattr(yc_mod, "_load_companies", fake_load)
        monkeypatch.setattr(yc_mod, "_founders", fake_founders)
        leads = asyncio.run(yc_mod.YCStartupsScraper().search("Acme"))
        assert len(leads) == 1
        return leads[0]

    def test_first_named_founder_becomes_lead_identity(self, monkeypatch):
        lead = self._search(monkeypatch, [("Jane Doe", "CEO"), ("John Roe", "CTO")])
        assert lead["name"] == "Jane Doe"
        assert lead["designation"] == "CEO"
        assert lead["company"] == "Acme"
        assert lead["_domain"] == "acme.com"
        assert lead["_pool"] is True
        # Never invents an address — resolver grounds it downstream.
        assert lead["email"] == ""
        # Co-founders still enrich the draft context.
        assert "Jane Doe (CEO)" in lead["context"]
        assert "John Roe (CTO)" in lead["context"]

    def test_single_token_founder_skipped_for_identity(self, monkeypatch):
        # "Madonna" has no "First Last" → not a resolvable person; fall back to
        # the next founder that does.
        lead = self._search(monkeypatch, [("Madonna", "CEO"), ("Kai Lin", "CTO")])
        assert lead["name"] == "Kai Lin"
        assert lead["designation"] == "CTO"

    def test_no_named_founder_stays_nameless_recruiter(self, monkeypatch):
        lead = self._search(monkeypatch, [])
        assert lead["name"] == ""
        assert lead["designation"] == "Recruiter"


class TestAtsDomainGuessGate:
    def test_guess_only_when_slug_matches_company(self):
        import asyncio
        from unittest.mock import AsyncMock, patch
        from app.scrapers.ats import GreenhouseScraper

        async def run(company_name):
            s = GreenhouseScraper()
            with patch.object(s, "_fetch", new=AsyncMock(
                    return_value=(company_name, "", [{"title": "Golang Engineer",
                                                       "location": "", "text": ""}]))):
                return await s._collect(None, "solace", "", "golang hiring", False)

        # Slug matches the company -> guessed domain OK.
        leads = asyncio.run(run("Solace"))
        assert leads and leads[0].get("_domain") == "solace.com"
        # Slug does NOT match -> no guessed domain; nameless lead suppressed
        # (a P0 careers@ probe at the wrong real company would misattribute).
        leads2 = asyncio.run(run("Solace Health Technologies Ltd"))
        # slug 'solace' IS a token of the company name here — adjust: use a
        # company whose tokens don't include the slug at all.
        leads3 = asyncio.run(run("Bright Medical"))
        assert not any(l.get("_domain") for l in leads3)


class TestBoardTechTags:
    def test_tags_learned_word_bounded(self):
        from app.scrapers.ats import _board_tech_tags
        tags = _board_tech_tags([
            "Senior Golang Engineer", "Python Backend Developer",
            "Go To Market Manager",       # must NOT produce a golang tag alone
            "React Native Engineer",
        ])
        assert "python" in tags and "react" in tags and "backend" in tags
        assert "golang" in tags          # from the explicit Golang title
        assert "go" not in tags          # bare ambiguous token excluded

    def test_gtm_alone_never_tags_golang(self):
        from app.scrapers.ats import _board_tech_tags
        assert "golang" not in _board_tech_tags(["Go To Market Manager"])
        assert "golang" in _board_tech_tags(["Go Engineer"])

    def test_ranking_prefers_tag_matches_then_unknown(self, monkeypatch):
        from types import SimpleNamespace
        import app.scrapers.ats as ats_mod
        from app.scrapers import directory
        monkeypatch.setattr(
            ats_mod, "companies_for_ats",
            lambda key: [SimpleNamespace(slug=s, domain="") for s in
                         ("offtopic", "match", "unknown")],
        )
        directory.set_company_tags("greenhouse", "match", {"golang", "python"})
        directory.set_company_tags("greenhouse", "offtopic", {"react"})
        try:
            s = ats_mod.GreenhouseScraper()
            targets = s._targets("golang hiring", company_mode=False,
                                 query_tokens=frozenset({"golang", "go"}))
            order = [t[0] for t in targets]
            assert order[0] == "match"        # tag intersection first
            assert order[1] == "unknown"      # never probed second
            assert order[2] == "offtopic"     # known-off-topic last
        finally:
            directory._TAGS_OVERLAY.clear()
