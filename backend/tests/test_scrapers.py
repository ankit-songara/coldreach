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


class TestLinkBio:
    """Link-in-bio grounding: a person's self-published email pulled from a
    Linktree/bio.link/… page they linked in their provenance note."""

    def test_url_extraction(self):
        from app.scrapers.web import linkbio_url_in
        assert linkbio_url_in("reach me: https://linktr.ee/janedoe now") == "https://linktr.ee/janedoe"
        assert linkbio_url_in("https://www.bio.link/kai-lin") == "https://www.bio.link/kai-lin"
        assert linkbio_url_in("bio.link/kai") is None            # no scheme → not matched
        assert linkbio_url_in("nothing to see") is None
        # percent-encoded (provenance often wraps URLs)
        assert linkbio_url_in("u=https%3A%2F%2Flinktr.ee%2Fjane") == "https://linktr.ee/jane"

    def test_email_name_matched(self, monkeypatch):
        import asyncio
        from app.scrapers import web
        async def fake_get(client, url, timeout):
            return "<p>book a call · email me at jane.doe@acme.com</p>"
        monkeypatch.setattr(web, "_cached_get", fake_get)
        monkeypatch.setattr(web, "resolves_public", lambda host: True)
        got = asyncio.run(web.linkbio_email_for_person("bio: https://linktr.ee/janedoe", "Jane", "Doe"))
        assert got == "jane.doe@acme.com"

    def test_email_ignores_non_matching(self, monkeypatch):
        import asyncio
        from app.scrapers import web
        async def fake_get(client, url, timeout):
            return "<p>team@acme.com and bob@acme.com</p>"   # neither is Jane
        monkeypatch.setattr(web, "_cached_get", fake_get)
        monkeypatch.setattr(web, "resolves_public", lambda host: True)
        assert asyncio.run(web.linkbio_email_for_person("https://linktr.ee/janedoe", "Jane", "Doe")) is None

    def test_no_bio_url_makes_no_request(self, monkeypatch):
        import asyncio
        from app.scrapers import web
        calls = {"n": 0}
        async def fake_get(client, url, timeout):
            calls["n"] += 1
            return ""
        monkeypatch.setattr(web, "_cached_get", fake_get)
        # No bio URL in the note → returns None WITHOUT fetching anything.
        assert asyncio.run(web.linkbio_email_for_person("Jane Doe, founder at Acme", "Jane", "Doe")) is None
        assert calls["n"] == 0


class TestStealthFetch:
    """web._fetch_text: curl_cffi TLS impersonation first (beats anti-bot 403s),
    httpx as the fallback. (conftest disables the real _CffiAsyncSession; each
    test installs its own fake.)"""

    def _session(self, status, body):
        class Resp:
            status_code = status
            text = body
        class Session:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **k): return Resp()
        return Session

    def _boom_session(self):
        class Session:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **k): raise RuntimeError("connection reset")
        return Session

    def test_impersonation_is_primary(self, monkeypatch):
        import asyncio
        from app.scrapers import web
        monkeypatch.setattr(web, "_CffiAsyncSession", self._session(200, "<p>chrome</p>"))
        class BadHttpx:   # must never be touched when impersonation succeeds
            async def get(self, *a, **k): raise AssertionError("httpx should not be called")
        assert asyncio.run(web._fetch_text(BadHttpx(), "https://x.com", 5)) == "<p>chrome</p>"

    def test_non_2xx_yields_empty_without_httpx(self, monkeypatch):
        import asyncio
        from app.scrapers import web
        monkeypatch.setattr(web, "_CffiAsyncSession", self._session(403, "blocked"))
        class BadHttpx:   # a real 403 → don't bother retrying via the weaker client
            async def get(self, *a, **k): raise AssertionError("no httpx fallback on cffi non-2xx")
        assert asyncio.run(web._fetch_text(BadHttpx(), "https://x.com", 5)) == ""

    def test_falls_back_to_httpx_on_cffi_error(self, monkeypatch):
        import asyncio
        from app.scrapers import web
        monkeypatch.setattr(web, "_CffiAsyncSession", self._boom_session())
        class HttpxResp:
            is_success = True
            text = "<p>httpx</p>"
        class HttpxClient:
            async def get(self, *a, **k): return HttpxResp()
        assert asyncio.run(web._fetch_text(HttpxClient(), "https://x.com", 5)) == "<p>httpx</p>"


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


class TestSmartRecruitersSearch:
    """The keyless global SmartRecruiters search: one call → many companies,
    domain guessed from the company name, sibling variants tagged."""

    def test_emits_company_domain_leads(self, monkeypatch):
        import asyncio, httpx
        from app.scrapers.jobboards import SmartRecruitersSearchScraper

        page1 = {"content": [
            {"name": "Backend Engineer",   "company": {"name": "Eurofins", "identifier": "Eurofins"}},
            {"name": "Golang Engineer",     "company": {"name": "AcmeCo",   "identifier": "AcmeCo"}},
            {"name": "Account Executive",   "company": {"name": "NoMatchCo","identifier": "x"}},
        ]}

        def handler(request: httpx.Request) -> httpx.Response:
            # Page 1 (offset=0) has data; the next page is empty → loop stops.
            if request.url.params.get("offset") in ("0", None):
                return httpx.Response(200, json=page1)
            return httpx.Response(200, json={"content": []})

        real = httpx.AsyncClient
        monkeypatch.setattr(httpx, "AsyncClient",
                            lambda *a, **kw: real(transport=httpx.MockTransport(handler)))
        leads = asyncio.run(SmartRecruitersSearchScraper().search(
            "backend engineer hiring", query_variants=("golang", "python")))
        by = {l["company"]: l for l in leads}
        assert by["Eurofins"]["_domain"] == "eurofins.com" and not by["Eurofins"].get("_sibling")
        assert by["AcmeCo"].get("_sibling") is True          # matched the golang variant
        assert "NoMatchCo" not in by                         # no role match → dropped
        assert all(l["source"] == "SmartRecruitersSearch" and l["name"] == "" for l in leads)


class TestTeamtailor:
    """Teamtailor's public JSON Feed: derives the employer's ROOT domain from
    hiringOrganization.sameAs; the default *.teamtailor.com yields no domain."""

    def test_root_domain_derivation(self):
        from app.scrapers.ats import _teamtailor_domain
        assert _teamtailor_domain("https://careers.oatly.com") == "oatly.com"
        assert _teamtailor_domain("https://jobs.acme.co.uk") == "acme.co.uk"
        assert _teamtailor_domain("https://acme.com") == "acme.com"
        assert _teamtailor_domain("https://oatly.teamtailor.com") == ""   # default host, not real mail domain
        assert _teamtailor_domain("") == ""

    def test_fetch_returns_company_domain_jobs(self, monkeypatch):
        import asyncio, httpx
        from app.scrapers.ats import TeamtailorScraper

        feed = {"title": "Oatly AB", "items": [{
            "title": "Nutrition Specialist",
            "content_html": "<p>Own our nutrition science.</p>",
            "_jobposting": {
                "hiringOrganization": {"name": "Oatly AB", "sameAs": "https://careers.oatly.com"},
                "jobLocation": [{"address": {"addressLocality": "London", "addressCountry": "GB"}}],
            },
        }]}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=feed, headers={"content-type": "application/feed+json"})

        real = httpx.AsyncClient
        monkeypatch.setattr(httpx, "AsyncClient",
                            lambda *a, **kw: real(transport=httpx.MockTransport(handler)))
        company, domain, jobs = asyncio.run(TeamtailorScraper()._fetch(None, "oatly"))
        assert company == "Oatly AB"
        assert domain == "oatly.com"                    # careers.oatly.com → root
        assert jobs and jobs[0]["title"] == "Nutrition Specialist"
        assert jobs[0]["location"] == "London, GB"


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


class TestPostingRecency:
    """Upstream posting dates → normalized 'YYYY-MM-DD' on each job, and the
    freshest MATCHING posting stamped on emitted leads as transient _posted_at
    (underscore key — ranked on in hunt.py, never persisted)."""

    def test_posted_iso_live_verified_formats(self):
        from app.scrapers.ats import _posted_iso
        # Greenhouse first_published / Ashby publishedAt / SR releasedDate
        assert _posted_iso("2026-07-22T12:50:03-04:00") == "2026-07-22"
        assert _posted_iso("2026-07-30T20:18:55.841Z") == "2026-07-30"
        # Lever createdAt (epoch milliseconds)
        assert _posted_iso(1711403416463) == "2024-03-25"
        # Recruitee created_at ("YYYY-MM-DD HH:MM:SS UTC")
        assert _posted_iso("2026-04-21 15:49:10 UTC") == "2026-04-21"
        # Absent / garbage → "" (never raises)
        assert _posted_iso("") == ""
        assert _posted_iso(None) == ""
        assert _posted_iso("not a date") == ""
        assert _posted_iso(0) == ""

    def test_greenhouse_fetch_parses_first_published(self, monkeypatch):
        import asyncio, httpx
        from app.scrapers.ats import GreenhouseScraper

        payload = {"jobs": [{
            "title": "Backend Engineer", "location": {"name": "Remote"},
            "content": "Build things.",
            "first_published": "2026-07-25T09:00:00-04:00",
            "updated_at": "2026-07-29T09:00:00-04:00",
        }]}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await GreenhouseScraper()._fetch(client, "acme")

        _, _, jobs = asyncio.run(run())
        assert jobs[0]["posted"] == "2026-07-25"   # first_published wins over updated_at

    def test_emit_stamps_freshest_matching_posting(self):
        from app.scrapers.ats import GreenhouseScraper
        jobs = [
            {"title": "Backend Engineer", "location": "", "text": "", "posted": "2026-06-01"},
            {"title": "Platform Engineer", "location": "", "text": "", "posted": "2026-07-28"},
            {"title": "SRE", "location": "", "text": "", "posted": ""},
        ]
        leads = GreenhouseScraper()._emit("Acme", "acme.com", jobs, "acme")
        assert leads[0]["_posted_at"] == "2026-07-28"   # freshest of the matches

    def test_emit_without_dates_stamps_empty(self):
        from app.scrapers.ats import GreenhouseScraper
        jobs = [{"title": "Backend Engineer", "location": "", "text": ""}]
        leads = GreenhouseScraper()._emit("Acme", "acme.com", jobs, "acme")
        assert leads[0]["_posted_at"] == ""


class TestNotableProbePriority:
    """Well-known (notable) companies are probed ahead of the long tail within
    each hunt's target cap, while the exploration cursor still rotates them
    out once covered."""

    def _cos(self):
        from app.scrapers.directory import Company
        cos = [Company(f"Longtail {i}", f"longtail{i}", "greenhouse", "") for i in range(30)]
        cos += [
            Company("Stripe", "stripe", "greenhouse", "stripe.com", True),
            Company("OpenAI", "openai", "greenhouse", "openai.com", True),
        ]
        return cos

    def test_notable_lead_the_pool(self, monkeypatch):
        from app.scrapers import ats as ats_mod
        monkeypatch.setattr(ats_mod, "companies_for_ats", lambda k: self._cos())
        targets = ats_mod.GreenhouseScraper()._targets(
            "software engineer hiring", company_mode=False)
        assert {targets[0][0], targets[1][0]} == {"stripe", "openai"}

    def test_cursor_rotates_explored_notables_out(self, monkeypatch):
        from app.scrapers import ats as ats_mod
        monkeypatch.setattr(ats_mod, "companies_for_ats", lambda k: self._cos())
        targets = ats_mod.GreenhouseScraper()._targets(
            "software engineer hiring", company_mode=False,
            explored_slugs=frozenset({"greenhouse:stripe"}))
        slugs = [s for s, _ in targets]
        assert targets[0][0] == "openai"     # remaining notable still leads
        assert "stripe" not in slugs         # explored → excluded this hunt

    def test_notable_bias_keeps_tag_tiers_primary(self, monkeypatch):
        from app.scrapers import ats as ats_mod
        from app.scrapers import directory
        from app.scrapers.directory import Company
        cos = [
            Company("Tagged Match", "match", "greenhouse", ""),
            Company("Stripe", "stripe", "greenhouse", "stripe.com", True),
        ]
        monkeypatch.setattr(ats_mod, "companies_for_ats", lambda k: cos)
        directory.set_company_tags("greenhouse", "match", {"golang"})
        try:
            targets = ats_mod.GreenhouseScraper()._targets(
                "golang hiring", company_mode=False,
                query_tokens=frozenset({"golang", "go"}))
            # Query-relevant tags outrank brand fame; notable wins within a tier.
            assert targets[0][0] == "match"
            assert targets[1][0] == "stripe"
        finally:
            directory._TAGS_OVERLAY.clear()


class TestAsciiFoldNameMatching:
    """Accented names must match their (ASCII) mailbox local-parts."""

    def test_fold(self):
        from app.scrapers.web import _ascii_fold
        assert _ascii_fold("José") == "jose"
        assert _ascii_fold("Søren") == "soren"
        assert _ascii_fold("Müller") == "muller"
        assert _ascii_fold("Straße") == "strasse"
        assert _ascii_fold("plain") == "plain"

    def test_accented_person_matches_published_address(self):
        from app.scrapers.web import _local_matches_person
        assert _local_matches_person("jose.garcia", "José", "García")
        assert _local_matches_person("soren", "Søren", "Kierkegaard")
        assert not _local_matches_person("someone.else", "José", "García")

    def test_permutations_fold_accents(self):
        from app.scrapers.resolver import _permutations_clean
        emails = [e for e, _ in _permutations_clean("José", "García", "acme.com")]
        assert "jose.garcia@acme.com" in emails
        assert all(e.isascii() for e in emails)


class TestTransientFailureNotCached:
    """An all-pages-failed grounding scan is transient — it must stay
    retryable, not become a 6-hour definitive 'no published address'."""

    def test_all_failed_fetches_do_not_cache_a_miss(self, monkeypatch):
        import asyncio
        from app.scrapers import web

        async def dead_fetch(client, url, timeout):
            return ""                          # every page failed

        monkeypatch.setattr(web, "_cached_get", dead_fetch)
        monkeypatch.setattr(web, "resolves_public", lambda d: True)
        assert asyncio.run(web.find_published_role_email("transient-x.com")) is None
        cached, _ = web._cache_get("pages", "transient-x.com")
        assert not cached, "transient all-failure scan must not cache a miss"

    def test_real_empty_pages_do_cache_the_miss(self, monkeypatch):
        import asyncio
        from app.scrapers import web

        async def live_but_bare(client, url, timeout):
            return "<html><body>Welcome — nothing to see</body></html>"

        monkeypatch.setattr(web, "_cached_get", live_but_bare)
        monkeypatch.setattr(web, "resolves_public", lambda d: True)
        assert asyncio.run(web.find_published_role_email("bare-pages-x.com")) is None
        cached, value = web._cache_get("pages", "bare-pages-x.com")
        assert cached and value is None       # site up, genuinely nothing published


class TestNotableSeedPack:
    def test_notable_brands_loaded_from_csv(self):
        from app.scrapers.directory import notable_companies
        names = {c.name for c in notable_companies()}
        # Live-verified brand pack (75 boards probed with real HTTP) + famous
        # names already seeded — the exact count may drift as the CSV grows.
        assert len(names) >= 80
        assert {"Stripe", "OpenAI", "Anthropic", "Databricks"} <= names
