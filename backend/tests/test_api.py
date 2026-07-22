"""Integration tests for ColdReach API routes."""

import pytest


class TestHealth:
    def test_health_returns_ok(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestContacts:
    def test_list_empty(self, auth_client):
        r = auth_client.get("/api/contacts")
        assert r.status_code == 200
        assert r.json() == []

    def test_create_contact(self, auth_client):
        r = auth_client.post("/api/contacts", json={
            "name": "Priya Sharma",
            "email": "priya@startup.com",
            "designation": "CTO",
            "company": "StartupCo",
        })
        assert r.status_code == 201
        data = r.json()
        assert data["email"] == "priya@startup.com"
        assert data["status"] == "new"

    def test_duplicate_email_returns_existing(self, auth_client):
        payload = {"name": "Test", "email": "test@co.com", "designation": "HR", "company": "Co"}
        r1 = auth_client.post("/api/contacts", json=payload)
        r2 = auth_client.post("/api/contacts", json=payload)
        assert r1.json()["id"] == r2.json()["id"]

    def test_update_status(self, auth_client):
        contact = auth_client.post("/api/contacts", json={
            "name": "Ankit", "email": "ankit@co.com", "designation": "TA", "company": "Co",
        }).json()
        r = auth_client.patch(f"/api/contacts/{contact['id']}", json={"status": "emailed"})
        assert r.status_code == 200
        assert r.json()["status"] == "emailed"

    def test_delete_contact(self, auth_client):
        contact = auth_client.post("/api/contacts", json={
            "name": "Del", "email": "del@co.com", "designation": "HR", "company": "Co",
        }).json()
        r = auth_client.delete(f"/api/contacts/{contact['id']}")
        assert r.status_code == 204
        assert auth_client.get("/api/contacts").json() == []

    def test_delete_nonexistent_returns_404(self, auth_client):
        r = auth_client.delete("/api/contacts/9999")
        assert r.status_code == 404


class TestDemoSeed:
    def test_seed_populates_then_clears(self, auth_client):
        r = auth_client.post("/api/demo/seed")
        assert r.status_code == 200
        body = r.json()
        assert body["seeded"] is True and body["contacts"] > 0

        contacts = auth_client.get("/api/contacts").json()
        assert len(contacts) == body["contacts"]
        # Seeded addresses must be non-routable so demo data can't reach a real person.
        assert all(c["email"].endswith(".example") for c in contacts)

        # Seeding again is a no-op (doesn't duplicate).
        assert auth_client.post("/api/demo/seed").json()["seeded"] is False

        # Clearing removes exactly the seeded rows.
        cleared = auth_client.delete("/api/demo").json()["cleared"]
        assert cleared == body["contacts"]
        assert auth_client.get("/api/contacts").json() == []


class TestCompaniesDirectory:
    def test_add_extends_directory_then_delete(self, auth_client):
        from app.scrapers import directory

        # A company not present in the curated seed.
        assert not directory.is_known("greenhouse", "acmewidgets")
        before = auth_client.get("/api/companies").json()

        r = auth_client.post("/api/companies", json={
            "name": "Acme Widgets", "slug": "acmewidgets",
            "ats": "greenhouse", "domain": "acmewidgets.com",
        })
        assert r.status_code == 201, r.text
        cid = r.json()["id"]

        # It's now live in the directory (lookup + role-mode scan) and listed.
        assert directory.is_known("greenhouse", "acmewidgets")
        assert directory.lookup("Acme Widgets") is not None
        after = auth_client.get("/api/companies").json()
        assert after["total"] == before["total"] + 1          # delta, not absolute
        assert after["seed_count"] == before["seed_count"]    # CSV seed unchanged
        assert any(c["slug"] == "acmewidgets" for c in after["companies"])

        # Unknown ATS is rejected.
        assert auth_client.post("/api/companies", json={
            "name": "X", "slug": "x", "ats": "workday"}).status_code == 400

        # Delete removes it from the live directory.
        assert auth_client.delete(f"/api/companies/{cid}").status_code == 204
        assert not directory.is_known("greenhouse", "acmewidgets")


class TestResumeExtract:
    def test_unsupported_format_returns_400(self, auth_client):
        r = auth_client.post(
            "/api/resume/extract",
            files={"file": ("resume.txt", b"Some text", "text/plain")},
        )
        assert r.status_code == 400

    def test_empty_pdf_returns_422(self, auth_client):
        # A minimal valid PDF with no text
        empty_pdf = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\nxref\n0 2\n0000000000 65535 f\n0000000009 00000 n\ntrailer\n<< /Size 2 /Root 1 0 R >>\nstartxref\n9\n%%EOF"
        r = auth_client.post(
            "/api/resume/extract",
            files={"file": ("resume.pdf", empty_pdf, "application/pdf")},
        )
        # Either extracts empty (422) or parses it
        assert r.status_code in (200, 422)


class TestRoleFilter:
    """Query-relevance role classification (hunt.py)."""

    @pytest.mark.parametrize("designation, expected", [
        ("Software Engineer",     {"engineering"}),
        ("Backend Engineer",      {"engineering"}),
        ("Engineering Manager",   {"engineering", "management"}),
        ("Head of Engineering",   {"engineering", "management"}),
        ("Founder",               {"founder_exec"}),
        ("CTO",                   {"founder_exec"}),
        ("Technical Recruiter",   {"recruiting"}),
        ("People Ops",            {"recruiting"}),
        ("Product Manager",       {"product", "management"}),
        ("UX Designer",           {"design"}),
        ("Data Scientist",        {"data"}),
        ("",                      set()),
    ])
    def test_role_families(self, designation, expected):
        from app.api.hunt import _role_families
        assert _role_families(designation) == expected

    @pytest.mark.parametrize("designation, expected_rank", [
        ("Engineering Manager", 0),   # matches management
        ("Hiring Manager",      0),   # matches management
        ("VP of Sales",         0),   # "vp" → management
        ("Founder",             1),   # gatekeeper
        ("Technical Recruiter", 1),   # gatekeeper
        ("",                    2),   # unknown → kept, ranked last
        ("Software Engineer",   None),  # off-target IC → dropped
        ("Backend Engineer",    None),  # off-target IC → dropped
        ("UX Designer",         None),  # off-target → dropped
    ])
    def test_management_search_ranks_and_drops(self, designation, expected_rank):
        from app.api.hunt import _role_match_rank
        assert _role_match_rank(designation, "management") == expected_rank

    def test_engineering_search_keeps_engineers_and_gatekeepers(self):
        from app.api.hunt import _role_match_rank
        assert _role_match_rank("Software Engineer", "engineering") == 0
        assert _role_match_rank("Engineering Manager", "engineering") == 0
        assert _role_match_rank("Founder", "engineering") == 1          # gatekeeper kept
        assert _role_match_rank("Product Manager", "engineering") is None  # off-target dropped

    @pytest.mark.parametrize("designation, priority", [
        ("Founder",                        0),   # named people lead the results
        ("CTO",                            0),
        ("Technical Recruiter",            1),   # named HR/TA person
        ("Software Engineer",              2),
        ("Office Manager",                 3),   # any other named person
        ("Talent/Recruiting (role inbox)", 4),   # grounded fallback, after people
        ("Company Inbox (role inbox)",     4),
    ])
    def test_named_people_sort_before_role_inboxes(self, designation, priority):
        from app.api.hunt import _desig_priority
        assert _desig_priority(designation) == priority

    @pytest.mark.parametrize("query, expected", [
        # the reported bug: a bare domain word must carry role intent on its own
        ("product",                        "product"),
        ("product manager hiring",         "product"),   # domain wins over generic "manager"
        ("hiring manager",                 "management"),
        ("management position",            "management"),
        ("react engineer hiring",          "engineering"),
        ("devops kubernetes hiring",       "engineering"),
        ("android developer hiring",       "engineering"),
        ("founding engineer",              "engineering"),  # domain wins over generic "founding"
        ("founder",                        "founder_exec"),
        ("technical recruiter",            "recruiting"),
        ("ux designer",                    "design"),
        # ambiguous or no signal at all -> no inference, filter stays off
        ("data engineer hiring",           ""),   # two domain families (data + engineering)
        ("machine learning engineer",      ""),   # two domain families (data + engineering)
        ("golang hiring",                  ""),   # no recognizable family
        ("Linear",                         ""),   # company-name search
        ("Supabase",                       ""),
    ])
    def test_infer_role_from_query(self, query, expected):
        from app.api.hunt import _infer_role_from_query
        assert _infer_role_from_query(query) == expected

    @pytest.mark.parametrize("role_filter, query, expected", [
        # explicit dropdown always wins, even against contradicting query text
        ("management", "react engineer hiring", "management"),
        ("engineering", "product manager hiring", "engineering"),
        # dropdown left on "any" (empty or garbage) -> falls back to inference
        ("", "product", "product"),
        ("any", "product", "product"),
        ("not_a_real_value", "hiring manager", "management"),
        # neither carries signal -> no filter
        ("", "Linear", ""),
        ("", "", ""),
    ])
    def test_resolve_target_role_precedence(self, role_filter, query, expected):
        from app.api.hunt import _resolve_target_role
        assert _resolve_target_role(role_filter, query) == expected


class TestCompanyDomainGuess:
    """
    Universal careers@/jobs@ fallback: guess a company's own domain so a
    company-name hunt never comes back empty just because no scraper had
    coverage for it (no GitHub presence, no HN post, not on a supported ATS).
    """

    def test_known_company_uses_real_directory_domain(self):
        from app.api.hunt import _guess_company_domain
        from app.scrapers import directory
        # Any seeded company with a real domain proves the directory path wins
        # over guessing — pick one straight from the live seed rather than
        # assuming a specific name is present.
        known = next((c for c in directory.all_companies() if c.domain), None)
        assert known is not None, "seed has no company with a domain — test needs a fixture"
        assert _guess_company_domain(known.name) == known.domain

    @pytest.mark.parametrize("query, expected", [
        ("Zyloquartz",         "zyloquartz.com"),   # single made-up word
        ("Acme Widgets Corp",  "acme.com"),         # short-name convention, not "acmewidgetscorp.com"
        ("Brightmind AI",      "brightmind.com"),   # first word alone, not "brightmindai.com"
    ])
    def test_unknown_company_guesses_short_name_dot_com(self, query, expected):
        from app.api.hunt import _guess_company_domain
        from app.scrapers import directory
        assert directory.lookup(query) is None, "test assumes this name isn't in the seed"
        assert _guess_company_domain(query) == expected

    def test_empty_query_yields_no_guess(self):
        from app.api.hunt import _guess_company_domain
        assert _guess_company_domain("") == ""


class TestRoleInboxFallback:
    """Role-address ordering and the catch-all fix (careers@ etc. must not be
    discarded just because the domain accepts everything)."""

    def test_careers_is_tried_first(self):
        from app.api.hunt import _ROLE_ADDRESSES
        assert _ROLE_ADDRESSES[0] == "careers"

    def test_alt_tlds_cover_in_and_org(self):
        from app.api.hunt import _ALT_TLDS
        assert ".in" in _ALT_TLDS
        assert ".org" in _ALT_TLDS

    def test_catchall_domain_returns_guess_instead_of_none(self, monkeypatch):
        import asyncio
        from app.api import hunt as hunt_mod
        from app.scrapers.resolver import ResolutionCache

        async def fake_mx(self, domain):
            return ["mx.example.com"]

        async def fake_catch_all(self, domain, mx):
            return True   # every local part is accepted

        async def fake_page_emails(domain):
            return []     # no real named person found on the company's site

        monkeypatch.setattr(ResolutionCache, "mx", fake_mx)
        monkeypatch.setattr(ResolutionCache, "catch_all", fake_catch_all)
        monkeypatch.setattr(hunt_mod, "emails_from_company_pages", fake_page_emails)

        cache = ResolutionCache()
        result = asyncio.run(hunt_mod._resolve_domain_contact(
            {"name": "", "company": "Acme", "_domain": "acme.com"}, cache,
        ))
        assert result is not None, "catch-all domain must not discard the lead entirely"
        assert result["email"] == "careers@acme.com"
        assert result["email_status"] == "risky"

    def test_vercel_ungrounded_lead_is_dropped_not_guessed(self, monkeypatch):
        """
        THE bounce-storm rule: on Vercel (port 25 blocked → no SMTP, no
        catch-all detection) a lead with no published/searchable/Hunter
        evidence must be DROPPED, never guessed. Every persisted email is
        grounded in real evidence. Confirmed here by NOT mocking _smtp_probe
        at all — if the code path reached it, this test would hang/fail on a
        real network call instead of returning immediately.
        """
        import asyncio
        from app.api import hunt as hunt_mod
        from app.scrapers.resolver import ResolutionCache

        async def fake_mx(self, domain):
            return ["mx.example.com"]

        async def fake_catch_all(self, domain, mx):
            return False   # not catch-all — nothing can ground the address

        async def fake_page_emails(domain):
            return []

        monkeypatch.setattr(ResolutionCache, "mx", fake_mx)
        monkeypatch.setattr(ResolutionCache, "catch_all", fake_catch_all)
        monkeypatch.setattr(hunt_mod, "emails_from_company_pages", fake_page_emails)
        monkeypatch.setenv("VERCEL", "1")

        cache = ResolutionCache()
        result = asyncio.run(hunt_mod._resolve_domain_contact(
            {"name": "", "company": "Acme", "_domain": "acme.com"}, cache,
        ))
        assert result is None, "an ungrounded lead must be dropped, not guessed"

    def test_domain_guess_lead_skips_expensive_page_scrape(self, monkeypatch):
        """
        Live-observed bug: a real corporate site's page-scrape step can burn
        the entire resolve budget before ever reaching the role-inbox check,
        starving the careers-inbox fallback of the tiny bit of time it needs.
        The synthetic lead (source="careers-inbox") must skip straight to the
        role-inbox check. A normal ATS-sourced lead (no such source tag) must
        still go through the page-scrape as before — proven by NOT mocking
        emails_from_company_pages and instead asserting it's simply never
        called for the careers-inbox lead.
        """
        import asyncio
        from app.api import hunt as hunt_mod
        from app.scrapers.resolver import ResolutionCache

        calls = []

        async def tracking_page_emails(domain):
            calls.append(domain)
            return []

        async def fake_mx(self, domain):
            return ["mx.example.com"]

        async def fake_catch_all(self, domain, mx):
            return True   # shortest path to a result, isolates the scrape-skip behaviour

        async def fake_published(domain):
            return None   # no grounded address on the company's own pages

        async def fake_web_search(domain, company=""):
            return None

        monkeypatch.setattr(hunt_mod, "emails_from_company_pages", tracking_page_emails)
        monkeypatch.setattr(hunt_mod, "find_published_role_email", fake_published)
        monkeypatch.setattr(hunt_mod, "search_role_email_on_web", fake_web_search)
        monkeypatch.setattr(ResolutionCache, "mx", fake_mx)
        monkeypatch.setattr(ResolutionCache, "catch_all", fake_catch_all)

        cache = ResolutionCache()
        result = asyncio.run(hunt_mod._resolve_domain_contact(
            {"name": "", "company": "Acme", "_domain": "acme.com", "source": "careers-inbox"},
            cache,
        ))
        assert result is not None
        assert calls == [], "careers-inbox lead must not trigger the page scrape"

        # A normal (non-careers-inbox) identity-only lead still gets scraped.
        calls.clear()
        asyncio.run(hunt_mod._resolve_domain_contact(
            {"name": "", "company": "Acme", "_domain": "acme.com", "source": "greenhouse/acme"},
            cache,
        ))
        assert calls == ["acme.com"], "non-fallback leads must keep the page-scrape step"

    def test_grounded_published_email_wins_over_blind_guess(self, monkeypatch):
        """
        Root fix for the bounce storm: when the company publishes a real
        hiring-inbox address on its own /careers or /jobs page (e.g. hr@ or
        hiring@, not necessarily careers@), that address must be used —
        confirmed, high confidence, valid status — instead of blindly
        guessing careers@domain.
        """
        import asyncio
        from app.api import hunt as hunt_mod
        from app.scrapers.resolver import ResolutionCache

        async def fake_mx(self, domain):
            return ["mx.example.com"]

        async def fake_published(domain):
            return "hr@acme.com"

        monkeypatch.setattr(ResolutionCache, "mx", fake_mx)
        monkeypatch.setattr(hunt_mod, "find_published_role_email", fake_published)

        cache = ResolutionCache()
        result = asyncio.run(hunt_mod._resolve_domain_contact(
            {"name": "", "company": "Acme", "_domain": "acme.com", "source": "careers-inbox"},
            cache,
        ))
        assert result is not None
        assert result["email"] == "hr@acme.com"
        assert result["email_status"] == "valid"
        assert result["designation"] == "Talent/Recruiting (role inbox)"
        assert result["confidence"] >= hunt_mod._MIN_RESOLVER_CONFIDENCE

    def test_hunter_generic_lookup_used_when_no_published_page(self, monkeypatch):
        import asyncio
        from app.api import hunt as hunt_mod
        from app.scrapers.resolver import ResolutionCache
        from app.scrapers.enricher import HunterEnricher

        async def fake_mx(self, domain):
            return ["mx.example.com"]

        async def fake_published(domain):
            return None

        async def fake_generic(self, domain):
            return "jobs@acme.com"

        async def fake_web_search(domain, company=""):
            return None

        monkeypatch.setattr(ResolutionCache, "mx", fake_mx)
        monkeypatch.setattr(hunt_mod, "find_published_role_email", fake_published)
        monkeypatch.setattr(hunt_mod, "search_role_email_on_web", fake_web_search)
        monkeypatch.setattr(HunterEnricher, "search_generic", fake_generic)
        monkeypatch.setattr(hunt_mod.settings, "hunter_api_key", "fake-key")

        cache = ResolutionCache()
        result = asyncio.run(hunt_mod._resolve_domain_contact(
            {"name": "", "company": "Acme", "_domain": "acme.com", "source": "careers-inbox"},
            cache,
        ))
        assert result is not None
        assert result["email"] == "jobs@acme.com"
        assert result["email_status"] == "valid"

    def test_catch_all_domain_keeps_deliverable_careers_lead(self, monkeypatch):
        """
        Catch-all is the ONE case where the conventional careers@ is kept
        without being published anywhere: the domain accepts every local
        part, so the address physically cannot bounce.
        """
        import asyncio
        from app.api import hunt as hunt_mod
        from app.scrapers.resolver import ResolutionCache

        async def fake_mx(self, domain):
            return ["mx.example.com"]

        async def fake_catch_all(self, domain, mx):
            return True

        async def fake_published(domain):
            return None

        async def fake_web_search(domain, company=""):
            return None

        monkeypatch.setattr(ResolutionCache, "mx", fake_mx)
        monkeypatch.setattr(ResolutionCache, "catch_all", fake_catch_all)
        monkeypatch.setattr(hunt_mod, "find_published_role_email", fake_published)
        monkeypatch.setattr(hunt_mod, "search_role_email_on_web", fake_web_search)
        monkeypatch.setattr(hunt_mod.settings, "hunter_api_key", "")

        cache = ResolutionCache()
        result = asyncio.run(hunt_mod._resolve_domain_contact(
            {"name": "", "company": "Acme", "_domain": "acme.com", "source": "careers-inbox"},
            cache,
        ))
        assert result is not None
        assert result["email"] == "careers@acme.com"
        assert result["designation"] == "Talent/Recruiting (role inbox)"
        assert result["confidence"] >= hunt_mod._MIN_RESOLVER_CONFIDENCE

    def test_legacy_unverified_guess_rows_still_ranked_last(self):
        """Pre-existing '(unverified guess)' contacts in user DBs (hunted
        between the labeling fix and the drop-ungrounded fix) must keep
        sorting below every real lead and using the formal template."""
        from app.api.hunt import _desig_priority
        from app.llm.prompts import get_designation_key
        assert _desig_priority("Talent/Recruiting (unverified guess)") == 5
        assert get_designation_key("Talent/Recruiting (unverified guess)") == "hiring_inbox"


class TestKeylessNamedGrounding:
    """Named leads (founders/HR/eng) resolve WITHOUT any API key: the person's
    real email is read off the company's own pages, and email-pattern learning
    falls back to an unauthenticated GitHub scan."""

    def test_person_email_found_on_company_page(self, monkeypatch):
        import asyncio
        from app.scrapers import web

        async def fake_pages(domain, timeout=8):
            return ["hello@acme.com", "jane.doe@acme.com", "press@acme.com"]
        monkeypatch.setattr(web, "emails_from_company_pages", fake_pages)

        got = asyncio.run(web.find_person_email("acme.com", "Jane", "Doe"))
        assert got == "jane.doe@acme.com"

    def test_person_email_none_when_no_name_match(self, monkeypatch):
        import asyncio
        from app.scrapers import web

        async def fake_pages(domain, timeout=8):
            return ["careers@acme.com", "info@acme.com"]   # no personal mailbox
        monkeypatch.setattr(web, "emails_from_company_pages", fake_pages)

        assert asyncio.run(web.find_person_email("acme.com", "Jane", "Doe")) is None

    @pytest.mark.parametrize("local, matches", [
        ("jane.doe", True), ("jdoe", True), ("jane", True), ("doe", True),
        ("jane-doe", True), ("j.doe", True), ("doe.jane", True),
        ("bob", False), ("sales", False), ("j", False),
    ])
    def test_local_matches_person(self, local, matches):
        from app.scrapers.web import _local_matches_person
        assert _local_matches_person(local, "Jane", "Doe") is matches

    def test_named_lead_grounds_from_page_without_keys(self, monkeypatch):
        """The end-to-end keyless path: a named lead (YC founder) whose email is
        published on the company page resolves to that real address — no SMTP,
        no Hunter, high confidence, marked valid."""
        import asyncio
        from app.api import hunt as hunt_mod
        from app.scrapers.resolver import ResolutionCache

        async def fake_mx(self, domain):
            return ["mx.example.com"]
        async def fake_find_person(domain, first, last, timeout=8):
            return "jane.doe@acme.com"
        monkeypatch.setattr(ResolutionCache, "mx", fake_mx)
        monkeypatch.setattr(hunt_mod, "find_person_email", fake_find_person)

        cache = ResolutionCache()
        result = asyncio.run(hunt_mod._resolve_domain_contact(
            {"name": "Jane Doe", "company": "Acme", "designation": "CEO",
             "_domain": "acme.com", "_pool": True},
            cache,
        ))
        assert result is not None
        assert result["email"] == "jane.doe@acme.com"
        assert result["email_status"] == "valid"
        assert result["designation"] == "CEO"
        assert result["confidence"] >= hunt_mod._MIN_RESOLVER_CONFIDENCE

    def test_keyless_github_scan_budget_capped(self, monkeypatch):
        import asyncio
        from app.scrapers import resolver as R
        from app.scrapers.resolver import ResolutionCache, _GH_KEYLESS_MAX_PER_HUNT
        from app.config import settings

        monkeypatch.setattr(settings, "github_token", "")   # force keyless path
        calls = {"n": 0}
        async def counting_keyless(domain):
            calls["n"] += 1
            return None
        monkeypatch.setattr(R, "learn_pattern_keyless", counting_keyless)

        cache = ResolutionCache()
        async def drive():
            for i in range(_GH_KEYLESS_MAX_PER_HUNT + 3):
                await cache.pattern(f"co{i}.com")
        asyncio.run(drive())
        assert calls["n"] == _GH_KEYLESS_MAX_PER_HUNT

    def test_org_guess_from_domain(self):
        from app.scrapers.resolver import _org_guess
        assert _org_guess("acme.com") == "acme"
        assert _org_guess("acme.co.uk") == "acme"
        assert _org_guess("supabase.io") == "supabase"


class TestHuntQuality:
    """Junk-email / test-identity / name-plausibility filters (quality > quantity)."""

    @pytest.mark.parametrize("email, junk", [
        # automated mailboxes — junk
        ("automated@acme.com",        True),
        ("notifications@acme.com",    True),
        ("notifications+ci@acme.com", True),
        ("alerts@acme.com",           True),
        ("newsletter@acme.com",       True),
        ("billing@acme.com",          True),
        ("system@acme.com",           True),
        # test fixtures — junk
        ("test@acme.com",             True),
        ("demo@acme.com",             True),
        ("qa@acme.com",               True),
        ("dummy@acme.com",            True),
        # reserved/test domains and scrape artifacts — junk
        ("sarah@example.com",         True),
        ("sarah@acme.test",          True),
        ("logo@2x.png",               True),
        # deliberate role inboxes — NOT junk (kept, labeled risky)
        ("talent@acme.com",           False),
        ("careers@acme.com",          False),
        ("hr@acme.com",               False),
        ("jobs@acme.com",             False),
        # real people — NOT junk (incl. names containing junk substrings)
        ("sarah.chen@acme.com",       False),
        ("devika@acme.com",           False),
    ])
    def test_is_junk_email(self, email, junk):
        from app.scrapers.base import is_junk_email
        assert is_junk_email(email) is junk

    @pytest.mark.parametrize("email, valid", [
        ("sarah.chen@acme.com",    True),
        ("automated@acme.com",     False),  # junk mailbox rejected outright
        ("test@example.com",       False),
        ("noreply@acme.com",       False),  # pre-existing skip list still applies
    ])
    def test_is_valid_email_rejects_junk(self, email, valid):
        from app.scrapers.base import is_valid_email
        assert is_valid_email(email) is valid

    @pytest.mark.parametrize("name, is_test", [
        ("Test User", True), ("John Doe", True), ("root", True),
        ("github actions", True), ("Priya Nair", False), ("", False),
    ])
    def test_is_test_identity(self, name, is_test):
        from app.scrapers.base import is_test_identity
        assert is_test_identity(name) is is_test

    @pytest.mark.parametrize("name, company, plausible", [
        ("Priya Nair",     "Vercel", True),
        ("Marcus Chen",    "",       True),
        ("Priya",          "Acme",   True),    # single real first name is fine
        ("Mary-Jane O'Neil", "",     True),
        # junk that must never be treated as a person:
        ("dev4life",       "",       False),   # digits → handle
        ("Contact",        "",       False),
        ("Test User",      "",       False),
        ("Hiring Team",    "Acme",   False),
        ("Acme Careers",   "Acme",   False),
        ("Vercel",         "Vercel", False),   # name IS the company
        ("Lead Recruiter", "",       False),
        ("john@acme.com",  "",       False),
        ("",               "",       False),
    ])
    def test_plausible_person_name(self, name, company, plausible):
        from app.scrapers.base import plausible_person_name
        assert plausible_person_name(name, company) is plausible


class TestGreeting:
    """Adaptive greeting tiers: person → 'Hi Name,' · team → 'Hi Co team,' · else 'Hi,'."""

    def _greeting(self, name, company=""):
        from app.llm.generator import _wrap
        wrapped = _wrap("A real body long enough to pass validation checks.",
                        name, "Sender", "", company)
        return wrapped.split("\n", 1)[0]

    def test_real_person_name(self):
        assert self._greeting("Priya Nair", "Vercel") == "Hi Priya,"

    def test_role_inbox_gets_team_greeting(self):
        assert self._greeting("Contact", "Vercel") == "Hi Vercel team,"

    def test_placeholder_name_unknown_company(self):
        assert self._greeting("Contact", "Unknown") == "Hi,"
        assert self._greeting("", "") == "Hi,"

    def test_company_as_name_not_greeted_as_person(self):
        # "Hi Vercel," must never happen — falls back to the team greeting.
        assert self._greeting("Vercel", "Vercel") == "Hi Vercel team,"

    def test_role_words_not_greeted(self):
        assert self._greeting("Talent", "Acme") == "Hi Acme team,"
        assert self._greeting("Hiring Manager", "Acme") == "Hi Acme team,"

    def test_overlong_company_falls_back_to_plain_hi(self):
        assert self._greeting("Contact", "Some Very Long Scraped Legal Entity Name Ltd") == "Hi,"

    def test_org_name_at_other_company_not_greeted_as_person(self):
        # Hand-added contact named "Acme Careers" at Brightlayer: never "Hi Acme,".
        assert self._greeting("Acme Careers", "Brightlayer") == "Hi Brightlayer team,"

    def test_handle_not_greeted_as_person(self):
        assert self._greeting("dev4life", "Stripe") == "Hi Stripe team,"


class TestDraftQuality:
    """Invisible post-generation pass: ungrounded company claims are removed."""

    def test_ungrounded_claim_stripped_when_no_context(self):
        from app.llm.quality import scrub_fabrications
        body = ("I noticed Brightlayer uses Postgres as its primary database. "
                "I cut p95 latency 40% at Acme.\n\nWorth a quick chat?")
        clean, fabricated = scrub_fabrications(body, company="Brightlayer", context="")
        assert len(fabricated) == 1
        assert "noticed" not in clean
        assert "cut p95 latency 40%" in clean          # candidate fact untouched
        assert "Worth a quick chat?" in clean

    def test_grounded_claim_kept(self):
        from app.llm.quality import scrub_fabrications
        body = "I saw you're hiring backend engineers. I ship Go services."
        ctx = "Job posting: Backend Engineer (Go), remote, Series A."
        clean, fabricated = scrub_fabrications(body, company="Acme", context=ctx)
        assert fabricated == []
        assert "hiring backend engineers" in clean

    def test_prefix_matching_tolerates_inflection(self):
        from app.llm.quality import scrub_fabrications
        # Body says "PostgreSQL", context says "Postgres" — still grounded.
        body = "I noticed your stack runs PostgreSQL under the hood."
        clean, fabricated = scrub_fabrications(body, company="Acme",
                                               context="Their stack: Postgres, Go, AWS.")
        assert fabricated == []

    def test_candidate_facts_never_touched(self):
        from app.llm.quality import scrub_fabrications
        body = ("I built a Stripe billing service handling 2M/yr. "
                "I cut API latency 40% at my last job.")
        clean, fabricated = scrub_fabrications(body, company="Acme", context="")
        assert fabricated == []
        assert clean == body

    def test_fluff_claim_stripped(self):
        from app.llm.quality import scrub_fabrications
        body = "I'm really impressed by your team. I ship Go services daily."
        clean, fabricated = scrub_fabrications(body, company="Acme", context="")
        assert len(fabricated) == 1
        assert "impressed" not in clean
        assert "Go services" in clean

    def test_company_direct_claim(self):
        from app.llm.quality import scrub_fabrications
        body = "Acme recently raised a Series B. I'd love to help you scale."
        clean, fabricated = scrub_fabrications(body, company="Acme", context="")
        assert len(fabricated) == 1
        assert "Series B" not in clean

    def test_ends_with_question(self):
        from app.llm.quality import ends_with_question
        assert ends_with_question("Some pitch.\n\nWorth a quick chat?") is True
        assert ends_with_question('Some pitch.\n\nWorth a quick chat?"') is True
        assert ends_with_question("Some pitch.\n\nLooking forward to it.") is False
        assert ends_with_question("") is False


class TestDraftScoring:
    """Deterministic reply-worthiness scoring + filler removal."""

    GOOD = ("You're hiring backend engineers for the payments rebuild at Ledgerly. "
            "I shipped exactly that: a Stripe billing service handling 2M/yr, and cut "
            "p95 latency 40% on the hot paths.\n\n"
            "If catch-up latency is the bottleneck, I'd start by moving the ledger "
            "writes off the ORM, that alone bought us 200ms.\n\n"
            "Want the one-page write-up of how we did it?")

    BAD = ("I'd like to share how my experience building scalable systems can help "
           "address latency concerns in your tech stack. I'm confident that my skills "
           "make me a great fit for your team. This experience taught me the importance "
           "of carefully evaluating trade-offs when scaling systems. "
           "I look forward to hearing from you.")

    def test_good_draft_beats_bad_draft(self):
        from app.llm.quality import score_draft
        good = score_draft(self.GOOD, "your payments rebuild",
                           word_range=(40, 95), company="Ledgerly",
                           context="rebuilding payments and billing stack")
        bad = score_draft(self.BAD, "Exciting Opportunity For Your Team",
                          word_range=(40, 95), company="Ledgerly",
                          context="rebuilding payments and billing stack")
        assert good >= 70, f"good draft scored {good}"
        assert bad < 40, f"bad draft scored {bad}"

    def test_overlong_draft_penalized(self):
        from app.llm.quality import score_draft
        body = ("Kafka moved 9 events. " * 40) + "Worth a chat?"
        assert score_draft(body, word_range=(40, 90)) < score_draft(
            "Kafka moved 9M events a day at Acme after my rewrite. Worth a chat?",
            word_range=(10, 90))

    def test_strip_filler_cuts_cover_letter_sentences(self):
        from app.llm.quality import strip_filler
        body = ("I cut p95 latency 40% at Acme. This experience taught me the "
                "importance of evaluating trade-offs. I'm confident I can help. "
                "Worth a quick chat?")
        clean, removed = strip_filler(body)
        assert removed == 2
        assert "taught me" not in clean and "confident" not in clean
        assert "cut p95 latency 40%" in clean and "Worth a quick chat?" in clean

    def test_strip_filler_keeps_factual_bodies_untouched(self):
        from app.llm.quality import strip_filler
        clean, removed = strip_filler(self.GOOD)
        assert removed == 0 and clean == self.GOOD

    def test_invented_candidate_numbers_stripped(self):
        from app.llm.quality import scrub_ungrounded_numbers
        resume = "Cut p95 latency 40 percent. Billing service handling 2M per year."
        body = ("I cut p95 latency 40 percent at Acme. "
                "I reduced average latency to under 500ms for every customer. "
                "Worth a quick chat?")
        clean, flagged = scrub_ungrounded_numbers(body, resume)
        assert len(flagged) == 1 and "500ms" in flagged[0]
        assert "40 percent" in clean and "Worth a quick chat?" in clean

    def test_grounded_numbers_survive(self):
        from app.llm.quality import scrub_ungrounded_numbers
        resume = "Cut p95 latency 40 percent. Billing handling 2M per year in transactions."
        body = "My billing service handled 2M per year. I cut latency 40 percent."
        clean, flagged = scrub_ungrounded_numbers(body, resume)
        assert flagged == [] and clean == body

    def test_small_bare_integers_ignored(self):
        from app.llm.quality import scrub_ungrounded_numbers
        # "15-minute chat" and "one of 3 options" must never be flagged.
        body = "Open to a 15-minute chat this week?"
        clean, flagged = scrub_ungrounded_numbers(body, "resume with no numbers")
        assert flagged == [] and clean == body

    def test_formal_register_keeps_application_style(self):
        from app.llm.generator import _clean_subject, _humanize
        from app.llm.prompts import get_designation_key, FORMAL_KEYS
        # Careers-inbox contacts route to the formal application template.
        assert get_designation_key("Talent/Recruiting (role inbox)") == "hiring_inbox"
        assert get_designation_key("Technical Recruiter") == "recruiter"
        assert {"hiring_inbox", "recruiter"} == set(FORMAL_KEYS)
        # Formal subjects keep Title Case and the dash; em-dash normalized.
        assert _clean_subject("SDE Application — Priya Nair", "Acme", formal=True) \
            == "SDE Application - Priya Nair"
        # Direct subjects still get the internal-note treatment.
        assert _clean_subject("Sde Application For Your Team", "Acme") \
            == "sde application for your team"
        # Formal bodies keep the single courtesy line; direct bodies lose it.
        courteous = "I hope you're doing well. I am asking about open SDE roles."
        assert "hope" in _humanize(courteous, formal=True).lower()
        assert "hope" not in _humanize(courteous).lower()

    def test_subject_detitlecased_preserving_acronyms_and_company(self):
        from app.llm.generator import _clean_subject
        assert _clean_subject("Backend Expertise For Brightmind AI", "Brightmind AI") \
            == "backend expertise for Brightmind AI"
        assert _clean_subject("Scaling LLM Eval Pipelines At Acme", "Acme") \
            == "scaling LLM eval pipelines at Acme"
        # already-natural subjects untouched
        assert _clean_subject("quick question about your data team") \
            == "quick question about your data team"


class TestRelevanceMatching:
    """Résumé facts are ranked against the recipient's company/role/context."""

    RESUME = """\
EXPERIENCE
- Built a RAG chatbot with LangChain and Postgres pgvector serving 10k queries/day
- Built a Stripe billing service handling 2M/yr in transactions end to end
- Cut p95 API latency 40% by moving hot paths off the ORM to raw SQL
- Migrated 30 services to Kubernetes on AWS, cutting deploy time from 1h to 6min

SKILLS
Go, Python, PyTorch, Postgres, Docker
"""

    def test_ai_company_ranks_llm_work_first(self):
        from app.llm.relevance import rank_relevant_facts
        facts, shared = rank_relevant_facts(
            self.RESUME,
            context="We're an AI platform hiring engineers to scale LLM inference.",
            designation="Engineering Manager", company="Brightmind AI",
        )
        assert facts, "AI context must produce a shortlist"
        assert "RAG chatbot" in facts[0]
        assert any(k in shared for k in ("llm", "ai"))

    def test_payments_company_ranks_billing_first(self):
        from app.llm.relevance import rank_relevant_facts
        facts, _ = rank_relevant_facts(
            self.RESUME,
            context="Fintech startup rebuilding its payments and billing stack.",
            designation="CTO", company="Ledgerly",
        )
        assert facts and "billing service" in facts[0]

    def test_devops_role_ranks_kubernetes_first(self):
        from app.llm.relevance import rank_relevant_facts
        facts, _ = rank_relevant_facts(
            self.RESUME,
            context="", designation="Head of Platform Engineering", company="Acme",
        )
        assert facts and "Kubernetes" in facts[0]

    def test_no_signal_returns_empty(self):
        from app.llm.relevance import rank_relevant_facts
        facts, shared = rank_relevant_facts(
            self.RESUME, context="", designation="Hiring Manager", company="Acme",
        )
        assert facts == [] and shared == []

    def test_extract_facts_skips_headers(self):
        from app.llm.relevance import extract_facts
        facts = extract_facts(self.RESUME)
        assert all(f != "EXPERIENCE" and f != "SKILLS" for f in facts)
        assert any("RAG chatbot" in f for f in facts)

    def test_rotation_is_deterministic_and_varies_by_seed(self):
        from app.llm.relevance import rotate_for_variety
        facts = ["a-fact", "b-fact", "c-fact"]
        r1 = rotate_for_variety(facts, "Priya|Acme")
        r2 = rotate_for_variety(facts, "Priya|Acme")
        assert r1 == r2                       # same contact → same email
        assert sorted(r1) == sorted(facts)    # nothing lost
        seeds = {tuple(rotate_for_variety(facts, s)) for s in
                 ("a|x", "b|y", "c|z", "d|w", "e|v")}
        assert len(seeds) > 1                 # different contacts → varied openers


class TestPatternMemory:
    """Persistent domain→email-pattern memory with bounce feedback."""

    def test_record_and_recall(self, db_session):
        from app.db.crud import record_domain_pattern, get_domain_patterns
        record_domain_pattern(db_session, "acme.com", "first.last", verified=True)
        assert get_domain_patterns(db_session, ["acme.com"]) == {"acme.com": "first.last"}
        # unknown domains simply aren't returned
        assert get_domain_patterns(db_session, ["nope.io"]) == {}

    def test_bounces_demote_pattern(self, db_session):
        from app.db.crud import (record_domain_pattern, get_domain_patterns,
                                 record_pattern_bounce)
        record_domain_pattern(db_session, "acme.com", "first.last", verified=False)  # count 1
        record_pattern_bounce(db_session, "someone@acme.com")                        # strike 1
        # strikes == confirmations → no longer trusted
        assert get_domain_patterns(db_session, ["acme.com"]) == {}

    def test_unverified_never_overwrites_verified(self, db_session):
        from app.db.crud import record_domain_pattern, get_domain_patterns
        record_domain_pattern(db_session, "acme.com", "first.last", verified=True)
        record_domain_pattern(db_session, "acme.com", "flast", verified=False)
        assert get_domain_patterns(db_session, ["acme.com"]) == {"acme.com": "first.last"}

    def test_verified_contradiction_replaces(self, db_session):
        from app.db.crud import record_domain_pattern, get_domain_patterns
        record_domain_pattern(db_session, "acme.com", "first.last", verified=False)
        record_domain_pattern(db_session, "acme.com", "flast", verified=True)
        assert get_domain_patterns(db_session, ["acme.com"]) == {"acme.com": "flast"}

    def test_cache_seeding(self):
        import asyncio
        from app.scrapers.resolver import ResolutionCache
        cache = ResolutionCache()
        cache.seed_pattern("acme.com", "first.last")
        assert asyncio.run(cache.pattern("acme.com")) == "first.last"
        assert cache.learned_patterns() == {"acme.com": "first.last"}


class TestCorsOriginRegex:
    """
    Regression test for a live production bug: the deployed frontend project
    (coldreach-niyp on Vercel) has multiple valid domains — a bare alias, a
    team-suffixed alias, a git-branch alias, and a brand-new hash URL on every
    deploy. CORS_ORIGINS was a static list that only had the bare alias;
    requests from the other two, equally-real domains got a 400 on preflight,
    which looks identical to "server unreachable" in the browser. Confirmed
    live via direct OPTIONS probes against the deployed backend. The regex
    fix must match every URL shape Vercel actually generates for this
    project, and must NOT match domains crafted to look similar.
    """

    @pytest.fixture
    def pattern(self):
        import re
        from app.config import settings
        return re.compile(settings.cors_origin_regex)

    @pytest.mark.parametrize("origin", [
        "https://coldreach-niyp.vercel.app",
        "https://coldreach-niyp-cold-reach.vercel.app",
        "https://coldreach-niyp-git-master-cold-reach.vercel.app",
        "https://coldreach-niyp-ovji96fdp-cold-reach.vercel.app",
    ])
    def test_matches_every_real_deployment_url(self, pattern, origin):
        assert pattern.fullmatch(origin)

    @pytest.mark.parametrize("origin", [
        "https://evil-coldreach-niyp.vercel.app",     # prefix spoof
        "https://coldreach-niyp.vercel.app.evil.com", # suffix-domain spoof
        "http://coldreach-niyp.vercel.app",           # wrong scheme
        "https://notcoldreach-niyp.vercel.app",
        "https://coldreach-niyp.vercelapp.com",
    ])
    def test_rejects_spoofed_or_wrong_domains(self, pattern, origin):
        assert not pattern.fullmatch(origin)

    def test_cors_preflight_allows_all_frontend_origins(self, client):
        """End-to-end: Starlette's CORSMiddleware actually honours the regex
        (fullmatch semantics — verified against the installed starlette
        version) for a real preflight request, for every real frontend URL."""
        for origin in [
            "https://coldreach-niyp.vercel.app",
            "https://coldreach-niyp-cold-reach.vercel.app",
            "https://coldreach-niyp-git-master-cold-reach.vercel.app",
        ]:
            r = client.options(
                "/api/health",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert r.status_code == 200, f"{origin} got {r.status_code}"
            assert r.headers.get("access-control-allow-origin") == origin


class TestResumeAttachment:
    """Original uploaded file is stored and attachable to formal emails."""

    # Minimal PDF with real extractable text so /extract succeeds.
    PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
           b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
           b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
           b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
           b"4 0 obj<</Length 44>>stream\nBT /F1 24 Tf 72 720 Td (Hello resume) Tj ET\nendstream endobj\n"
           b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
           b"trailer<</Root 1 0 R>>")

    def test_extract_stores_original_file(self, auth_client, db_session):
        from app.db.models import ResumeFile
        r = auth_client.post(
            "/api/resume/extract",
            files={"file": ("my-resume.pdf", self.PDF, "application/pdf")},
        )
        assert r.status_code == 200, r.text
        stored = db_session.query(ResumeFile).first()
        assert stored is not None, "original file bytes must be persisted"
        assert stored.filename == "my-resume.pdf"
        assert stored.mime == "application/pdf"
        assert stored.data == self.PDF

        # A second upload replaces, never duplicates.
        r2 = auth_client.post(
            "/api/resume/extract",
            files={"file": ("v2.pdf", self.PDF, "application/pdf")},
        )
        assert r2.status_code == 200
        db_session.expire_all()
        rows = db_session.query(ResumeFile).all()
        assert len(rows) == 1 and rows[0].filename == "v2.pdf"

    def test_build_message_with_attachment(self):
        from app.mailer import build_message
        msg = build_message("me@x.com", "you@y.com", "SDE Application - A",
                            "Body text", attachment=("resume.pdf", b"%PDF-fake"))
        raw = msg.as_string()
        assert 'filename="resume.pdf"' in raw
        assert "multipart/mixed" in raw
        assert "Body text" in raw

    def test_build_message_without_attachment_stays_plain(self):
        from app.mailer import build_message
        msg = build_message("me@x.com", "you@y.com", "subj", "Body")
        raw = msg.as_string()
        assert "multipart/alternative" in raw
        assert "attachment" not in raw


class TestPublishedRoleEmailScanner:
    """find_published_role_email: recognizes a REAL hiring-inbox address a
    company publishes on its own /careers or /jobs page, so the P0 lead can
    be grounded instead of blindly guessing careers@domain for every company
    (the direct cause of the production bounce storm this fixes)."""

    def test_recognizes_published_hiring_prefix(self, monkeypatch):
        import asyncio
        import httpx
        from app.scrapers import web as web_mod

        async def fake_resolves_public(domain):
            return True

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/careers":
                return httpx.Response(200, text="Reach us at hiring@acme.com for openings.")
            return httpx.Response(404)

        monkeypatch.setattr(web_mod, "resolves_public", lambda d: True)

        real_client = httpx.AsyncClient
        def fake_async_client(*a, **kw):
            kw.pop("timeout", None)
            kw.pop("follow_redirects", None)
            kw.pop("headers", None)
            return real_client(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(web_mod.httpx, "AsyncClient", fake_async_client)

        result = asyncio.run(web_mod.find_published_role_email("acme.com"))
        assert result == "hiring@acme.com"

    def test_ignores_off_domain_and_non_hiring_addresses(self, monkeypatch):
        import asyncio
        import httpx
        from app.scrapers import web as web_mod

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/careers":
                return httpx.Response(
                    200,
                    text="Press: press@othersite.com. Sales: sales@acme.com.",
                )
            return httpx.Response(404)

        monkeypatch.setattr(web_mod, "resolves_public", lambda d: True)
        real_client = httpx.AsyncClient
        def fake_async_client(*a, **kw):
            kw.pop("timeout", None)
            kw.pop("follow_redirects", None)
            kw.pop("headers", None)
            return real_client(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(web_mod.httpx, "AsyncClient", fake_async_client)

        result = asyncio.run(web_mod.find_published_role_email("acme.com"))
        assert result is None

    def test_private_domain_refused(self, monkeypatch):
        import asyncio
        from app.scrapers import web as web_mod
        monkeypatch.setattr(web_mod, "resolves_public", lambda d: False)
        result = asyncio.run(web_mod.find_published_role_email("internal.local"))
        assert result is None


class TestHunterGenericLookup:
    def test_search_generic_returns_domain_matching_address(self, monkeypatch):
        import asyncio
        import httpx
        from app.scrapers.enricher import HunterEnricher

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "data": {"emails": [
                    {"value": "careers@acme.com", "type": "generic"},
                    {"value": "someone@other.com", "type": "generic"},
                ]}
            })

        real_client = httpx.AsyncClient
        def fake_async_client(*a, **kw):
            kw.pop("timeout", None)
            return real_client(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)

        enricher = HunterEnricher("fake-key")
        result = asyncio.run(enricher.search_generic("acme.com"))
        assert result == "careers@acme.com"

    def test_search_generic_returns_none_on_failure(self, monkeypatch):
        import asyncio
        import httpx
        from app.scrapers.enricher import HunterEnricher

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401)

        real_client = httpx.AsyncClient
        def fake_async_client(*a, **kw):
            kw.pop("timeout", None)
            return real_client(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)

        enricher = HunterEnricher("fake-key")
        result = asyncio.run(enricher.search_generic("acme.com"))
        assert result is None

    def test_general_inbox_fallback_when_no_hiring_prefix(self, monkeypatch):
        """A published contact@/hello@ on the company's own site is a real
        deliverable address — used (labeled as a company inbox) when no
        dedicated hiring inbox is published, instead of a blind guess."""
        import asyncio
        import httpx
        from app.scrapers import web as web_mod

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path in ("/contact-us", "/contact"):
                return httpx.Response(200, text="Say hi: hello@acme.com or sales@acme.com")
            return httpx.Response(404)

        monkeypatch.setattr(web_mod, "resolves_public", lambda d: True)
        real_client = httpx.AsyncClient
        def fake_async_client(*a, **kw):
            kw.pop("timeout", None)
            kw.pop("follow_redirects", None)
            kw.pop("headers", None)
            return real_client(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(web_mod.httpx, "AsyncClient", fake_async_client)

        result = asyncio.run(web_mod.find_published_role_email("acme.com"))
        assert result == "hello@acme.com"

    def test_hiring_prefix_beats_general_inbox(self, monkeypatch):
        import asyncio
        import httpx
        from app.scrapers import web as web_mod

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="contact@acme.com and careers@acme.com")

        monkeypatch.setattr(web_mod, "resolves_public", lambda d: True)
        real_client = httpx.AsyncClient
        def fake_async_client(*a, **kw):
            kw.pop("timeout", None)
            kw.pop("follow_redirects", None)
            kw.pop("headers", None)
            return real_client(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(web_mod.httpx, "AsyncClient", fake_async_client)

        result = asyncio.run(web_mod.find_published_role_email("acme.com"))
        assert result == "careers@acme.com"

    def test_web_search_grounding_extracts_hiring_email(self, monkeypatch):
        """search_role_email_on_web: a hiring address seen in search-result
        snippets (job posts, directories) grounds the lead when the company's
        own site renders nothing server-side."""
        import asyncio
        import httpx
        from app.scrapers import web as web_mod

        def handler(request: httpx.Request) -> httpx.Response:
            assert "duckduckgo.com" in str(request.url)
            return httpx.Response(
                200, text="Apply at careers@acme.com ... or support@acme.com",
            )

        real_client = httpx.AsyncClient
        def fake_async_client(*a, **kw):
            kw.pop("timeout", None)
            kw.pop("follow_redirects", None)
            kw.pop("headers", None)
            return real_client(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(web_mod.httpx, "AsyncClient", fake_async_client)

        result = asyncio.run(web_mod.search_role_email_on_web("acme.com", "Acme"))
        assert result == "careers@acme.com"

    def test_web_search_ignores_support_and_other_domains(self, monkeypatch):
        import asyncio
        import httpx
        from app.scrapers import web as web_mod

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, text="support@acme.com careers@othersite.com",
            )

        real_client = httpx.AsyncClient
        def fake_async_client(*a, **kw):
            kw.pop("timeout", None)
            kw.pop("follow_redirects", None)
            kw.pop("headers", None)
            return real_client(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(web_mod.httpx, "AsyncClient", fake_async_client)

        result = asyncio.run(web_mod.search_role_email_on_web("acme.com", "Acme"))
        assert result is None


class TestGuessedContactPurge:
    """Startup cleanup of pre-grounding-fix blind guesses (the bounce storm)."""

    def test_purges_only_never_emailed_risky_role_inboxes(self, auth_client, db_session):
        from datetime import datetime
        from app.db.models import Contact, EmailDraft, User
        from app.db.migrations import purge_unverified_role_inbox_guesses

        user = db_session.query(User).first()
        rows = [
            # pre-fix blind guess, never emailed → purged
            Contact(user_id=user.id, name="Careers", email="careers@gone1.com",
                    designation="Talent/Recruiting (role inbox)",
                    company="Gone1", email_status="risky", status="new"),
            # already bounced (actioned) → kept for history
            Contact(user_id=user.id, name="Careers", email="careers@gone2.com",
                    designation="Talent/Recruiting (role inbox)",
                    company="Gone2", email_status="risky", status="bounced",
                    last_emailed_at=datetime(2026, 7, 18)),
            # post-fix grounded lead (valid) → kept
            Contact(user_id=user.id, name="Hiring", email="hiring@keep.com",
                    designation="Talent/Recruiting (role inbox)",
                    company="Keep", email_status="valid", status="new"),
            # post-fix honest guess → kept (already excluded from bulk send)
            Contact(user_id=user.id, name="Careers", email="careers@keep2.com",
                    designation="Talent/Recruiting (unverified guess)",
                    company="Keep2", email_status="risky", status="new"),
            # a normal person lead → untouched
            Contact(user_id=user.id, name="Sarah Chen", email="sarah@keep3.com",
                    designation="Recruiter", company="Keep3",
                    email_status="risky", status="new"),
        ]
        db_session.add_all(rows)
        db_session.commit()

        # Drafts for a purged and a kept contact — no FK cascade exists, so
        # the purge itself must remove the purged contact's drafts.
        gone = db_session.query(Contact).filter_by(email="careers@gone1.com").one()
        kept = db_session.query(Contact).filter_by(email="sarah@keep3.com").one()
        db_session.add_all([
            EmailDraft(user_id=user.id, contact_id=gone.id, subject="s", body="b"),
            EmailDraft(user_id=user.id, contact_id=kept.id, subject="s", body="b"),
        ])
        db_session.commit()
        gone_id, kept_id = gone.id, kept.id

        purged = purge_unverified_role_inbox_guesses(db_session)
        assert purged == 1
        remaining = {c.email for c in db_session.query(Contact).all()}
        assert "careers@gone1.com" not in remaining
        assert {"careers@gone2.com", "hiring@keep.com",
                "careers@keep2.com", "sarah@keep3.com"} <= remaining

        # The purged contact's draft went with it; the kept contact's survived.
        draft_owners = {d.contact_id for d in db_session.query(EmailDraft).all()}
        assert gone_id not in draft_owners
        assert kept_id in draft_owners

        # Idempotent — second run is a no-op.
        assert purge_unverified_role_inbox_guesses(db_session) == 0


class TestHuntSuggestions:
    def test_suggestions_returns_hiring_companies(self, auth_client, monkeypatch):
        import httpx
        from app.api import hunt as hunt_mod

        # -inf = "definitely expired". 0.0 only reads as expired when the
        # machine has been up longer than the TTL (monotonic() is since-boot).
        hunt_mod._suggest_cache.update(at=float("-inf"), pool=[])

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[
                {"legal": "meta"},
                {"company": "Acme Labs", "position": "Senior Backend Engineer"},
                {"company": "Acme Labs", "position": "SDE II"},
                {"company": "Marketing Co", "position": "Growth Marketer"},
                {"company": "Zed", "position": "Fullstack Developer"},
                {"company": "LOTHIAN BUSES LIMITED", "position": "Data Engineer"},
            ])

        real_client = httpx.AsyncClient
        def fake_async_client(*a, **kw):
            kw.pop("timeout", None)
            kw.pop("follow_redirects", None)
            kw.pop("headers", None)
            return real_client(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)

        r = auth_client.get("/api/hunt/suggestions")
        assert r.status_code == 200
        companies = r.json()["hiring_companies"]
        assert "Acme Labs" in companies          # engineering posting, deduped
        assert companies.count("Acme Labs") == 1
        assert "Marketing Co" not in companies   # non-engineering posting
        assert "Zed" in companies
        # Filed legal names are cleaned for display: suffix stripped, de-shouted.
        assert "Lothian Buses" in companies
        assert "LOTHIAN BUSES LIMITED" not in companies


class TestUncappedFeedScan:
    """Boards must scan the ENTIRE already-downloaded feed. The old first-10-
    matches-in-feed-order cap made repeat hunts return the same leads forever
    (feeds barely reorder day to day)."""

    def test_board_emits_every_matching_listing(self, monkeypatch):
        import asyncio
        import httpx
        from app.scrapers.jobboards import RemoteOKScraper

        listings = [
            {"company": f"Newco {i}", "position": "Backend Engineer",
             "tags": [], "description": "",
             "apply_url": f"https://newco{i}.io/jobs"}
            for i in range(25)
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[{"legal": "notice"}] + listings)

        real_client = httpx.AsyncClient
        def fake_async_client(*a, **kw):
            return real_client(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)

        leads = asyncio.run(RemoteOKScraper().search("backend engineer hiring"))
        assert len(leads) == 25   # the old cap stopped at 10


class TestHuntExclusionAwarePipeline:
    """Repeat hunts must not spend budget re-finding contacts the user owns:
    owned emails are skipped (once per unique address), nameless identity
    leads at role-inbox-owned domains are skipped, and the P0 careers@
    derivation skips only domains whose ROLE INBOX is owned — a mere person
    at a domain never suppresses its careers@ probe."""

    def _hunt_with_fake_scraper(self, auth_client, monkeypatch, fake_results):
        import asyncio
        from app.api import hunt as hunt_mod

        class FakeScraper:
            name = "Fake"
            async def safe_search(self, query, **kw):
                return fake_results

        monkeypatch.setattr(hunt_mod, "_build_scrapers", lambda key: [FakeScraper()])

        async def no_resolve(raw, cache):
            return None
        monkeypatch.setattr(hunt_mod, "_resolve_domain_contact", no_resolve)
        monkeypatch.setattr(hunt_mod, "verify_email", lambda e: "valid")
        # The cooldown map is module state but the test DB reuses user id=1 —
        # clear it before AND after so no other hunt test inherits a 429.
        hunt_mod._last_hunt.clear()
        try:
            r = auth_client.post("/api/hunt", json={"query": "backend hiring"})
            assert r.status_code == 200, r.text
            return r.json()
        finally:
            hunt_mod._last_hunt.clear()

    def test_owned_leads_skipped_new_leads_kept(self, auth_client, monkeypatch):
        # The user already owns this exact address.
        r = auth_client.post("/api/contacts", json={
            "name": "Careers", "email": "careers@ownedco-hx.com",
            "designation": "Talent/Recruiting (role inbox)", "company": "Ownedco",
        })
        assert r.status_code == 201, r.text

        owned_lead = {"name": "", "email": "careers@ownedco-hx.com", "company": "Ownedco",
                      "designation": "Recruiter", "source": "Fake", "context": ""}
        data = self._hunt_with_fake_scraper(auth_client, monkeypatch, [
            owned_lead,
            dict(owned_lead),   # same owned address from a "second board" — counts ONCE
            {"name": "Priya Nair", "email": "priya@freshstartup.io", "company": "Fresh",
             "designation": "Recruiter", "source": "Fake", "context": ""},
            # nameless identity lead at the role-inbox-owned domain → skipped
            {"name": "", "email": "", "company": "Ownedco", "designation": "Recruiter",
             "source": "Fake", "context": "", "_domain": "ownedco-hx.com"},
        ])

        emails = {c["email"] for c in data["contacts"]}
        assert "priya@freshstartup.io" in emails
        assert "careers@ownedco-hx.com" not in emails
        # 1 owned email (deduped across boards) + 1 nameless lead at the
        # owned role-inbox domain = 2, not 3.
        assert data["duplicates"] == 2
        assert data["total"] == 1

    def test_owned_person_does_not_suppress_careers_probe(self, auth_client, monkeypatch):
        from app.api import hunt as hunt_mod

        # The user owns a PERSON at ownedco-hx.com — NOT its role inbox.
        r = auth_client.post("/api/contacts", json={
            "name": "Sarah Chen", "email": "sarah.chen@ownedco-hx.com",
            "designation": "Recruiter", "company": "Ownedco",
        })
        assert r.status_code == 201, r.text

        probed_domains: list[str] = []
        async def spy_resolve(raw, cache):
            probed_domains.append(raw.get("_domain") or "")
            return None
        monkeypatch.setattr(hunt_mod, "_resolve_domain_contact", spy_resolve)

        class FakeScraper:
            name = "Fake"
            async def safe_search(self, query, **kw):
                # Nameless identity lead at ownedco-hx.com: NOT skipped (no owned
                # role inbox there) and its careers@ P0 probe must still run.
                return [{"name": "", "email": "", "company": "Ownedco",
                         "designation": "Recruiter", "source": "Fake",
                         "context": "", "_domain": "ownedco-hx.com"}]

        monkeypatch.setattr(hunt_mod, "_build_scrapers", lambda key: [FakeScraper()])
        monkeypatch.setattr(hunt_mod, "verify_email", lambda e: "valid")
        hunt_mod._last_hunt.clear()
        try:
            r = auth_client.post("/api/hunt", json={"query": "backend hiring"})
            assert r.status_code == 200, r.text
        finally:
            hunt_mod._last_hunt.clear()
        assert "ownedco-hx.com" in probed_domains


class TestHuntCursor:
    """DB-backed exploration memory: repeat hunts of the same query probe a
    fresh ATS slice; stale cursors (7-day TTL) are ignored lazily."""

    def test_roundtrip_and_merge(self, auth_client, db_session):
        from app.db.crud import get_explored_slugs, record_explored_slugs
        from app.db.models import User
        user = db_session.query(User).first()

        assert get_explored_slugs(db_session, user.id, "backend hiring") == set()
        record_explored_slugs(db_session, user.id, "backend hiring",
                              {"greenhouse:alpha", "lever:beta"})
        record_explored_slugs(db_session, user.id, "backend hiring",
                              {"greenhouse:gamma"})
        assert get_explored_slugs(db_session, user.id, "backend hiring") ==             {"greenhouse:alpha", "lever:beta", "greenhouse:gamma"}
        # Scoped per query: a different query has its own cursor.
        assert get_explored_slugs(db_session, user.id, "react hiring") == set()

    def test_stale_cursor_ignored_and_overwritten(self, auth_client, db_session):
        from datetime import datetime, timedelta
        from app.db.crud import get_explored_slugs, record_explored_slugs
        from app.db.models import HuntCursor, User
        user = db_session.query(User).first()

        record_explored_slugs(db_session, user.id, "golang hiring", {"greenhouse:old"})
        row = db_session.get(HuntCursor, (user.id, "golang hiring"))
        row.updated_at = datetime.utcnow() - timedelta(days=8)
        db_session.commit()

        assert get_explored_slugs(db_session, user.id, "golang hiring") == set()
        # A write over a stale cursor replaces it (old coverage expired).
        record_explored_slugs(db_session, user.id, "golang hiring", {"greenhouse:new"})
        assert get_explored_slugs(db_session, user.id, "golang hiring") == {"greenhouse:new"}


class TestHuntDuplicateContacts:
    """The all-duplicates dead end must SHOW which existing contacts matched."""

    def test_duplicate_contacts_hydrated_from_early_skips(self, auth_client, monkeypatch):
        from app.api import hunt as hunt_mod

        r = auth_client.post("/api/contacts", json={
            "name": "Careers", "email": "careers@dupco-hx.com",
            "designation": "Talent/Recruiting (role inbox)", "company": "Dupco",
        })
        assert r.status_code == 201, r.text
        owned_id = r.json()["id"]

        class FakeScraper:
            name = "Fake"
            async def safe_search(self, query, **kw):
                # Only an already-owned direct-email lead → all-duplicates hunt.
                return [{"name": "", "email": "careers@dupco-hx.com", "company": "Dupco",
                         "designation": "Recruiter", "source": "Fake", "context": ""}]

        monkeypatch.setattr(hunt_mod, "_build_scrapers", lambda key: [FakeScraper()])
        async def no_resolve(raw, cache):
            return None
        monkeypatch.setattr(hunt_mod, "_resolve_domain_contact", no_resolve)
        monkeypatch.setattr(hunt_mod, "verify_email", lambda e: "valid")
        hunt_mod._last_hunt.clear()
        try:
            resp = auth_client.post("/api/hunt", json={"query": "backend hiring"})
            assert resp.status_code == 200, resp.text
        finally:
            hunt_mod._last_hunt.clear()

        data = resp.json()
        assert data["total"] == 0 and data["duplicates"] >= 1
        dup = data["duplicate_contacts"]
        assert len(dup) == 1
        assert dup[0]["id"] == owned_id
        assert dup[0]["email"] == "careers@dupco-hx.com"
        assert dup[0]["company"] == "Dupco"
        assert "status" in dup[0]


class TestGroundedStatusSurvivesVerification:
    """
    Live-hunt bug found by manual verification: the resolver marks a
    grounded, actually-published address email_status="valid", but the
    downstream cheap-verifier merge only preserved a "risky" preset and
    silently overwrote "valid" with the verifier's own (usually "unknown")
    result -- so a real, found address showed up in the UI as "risky",
    indistinguishable from an unverified guess.
    """

    def test_grounded_valid_status_not_overwritten_by_cheap_verifier(self, monkeypatch, auth_client):
        from app.api import hunt as hunt_mod
        from app.scrapers.base import BaseScraper

        class FakeScraper(BaseScraper):
            name = "Fake"
            async def search(self, query, **_):
                return [{
                    "name": "", "email": "", "company": "Acme",
                    "designation": "", "source": "careers-inbox",
                    "_domain": "acme.com",
                }]

        async def fake_resolve(raw, cache):
            # Simulates a successful grounding (web-page scan / web search).
            return {**raw, "email": "hr@acme.com", "name": "Hr",
                    "designation": "Talent/Recruiting (role inbox)",
                    "confidence": 70, "email_status": "valid", "_domain": None}

        def fake_verify_email(email):
            # The cheap heuristic verifier has no SMTP/Hunter signal here —
            # this is the realistic "unknown" it returns absent a paid check.
            return "unknown"

        monkeypatch.setattr(hunt_mod, "_build_scrapers", lambda key: [FakeScraper()])
        monkeypatch.setattr(hunt_mod, "_resolve_domain_contact", fake_resolve)
        monkeypatch.setattr(hunt_mod, "verify_email", fake_verify_email)

        r = auth_client.post("/api/hunt", json={"query": "Acme"})
        assert r.status_code == 200
        contacts = r.json()["contacts"]
        acme = next(c for c in contacts if c["email"] == "hr@acme.com")
        assert acme["email_status"] == "valid", (
            "grounded lead's 'valid' status must survive the cheap-verifier merge"
        )


class TestSharedPageFetchCache:
    """The page-fetch cache halves per-hunt HTTP work: a company scheduled as
    BOTH a careers-inbox lead and an identity-only lead (which happens for
    every company by construction) must fetch each URL once, not twice."""

    def test_same_url_fetched_once_across_both_scanners(self, monkeypatch):
        import asyncio
        import httpx
        from app.scrapers import web as web_mod

        hits: dict[str, int] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            hits[url] = hits.get(url, 0) + 1
            # /careers is the ONE page both scanners fetch in common.
            if request.url.path == "/careers":
                return httpx.Response(200, text="jobs are at careers@acme.com")
            return httpx.Response(404)

        monkeypatch.setattr(web_mod, "resolves_public", lambda d: True)
        # Scrapling (headless) is excluded from the Vercel build, so production
        # uses the httpx path — force that path here too (and keep the test fast).
        async def no_scrapling(domain, timeout):
            return []
        monkeypatch.setattr(web_mod, "_scrape_scrapling", no_scrapling)
        real_client = httpx.AsyncClient
        def fake_async_client(*a, **kw):
            for k in ("timeout", "follow_redirects", "headers"):
                kw.pop(k, None)
            return real_client(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(web_mod.httpx, "AsyncClient", fake_async_client)

        async def run():
            # Careers-inbox scan and the full page scan for the SAME domain,
            # concurrently — exactly how a hunt schedules them.
            role, pages = await asyncio.gather(
                web_mod.find_published_role_email("acme.com"),
                web_mod.emails_from_company_pages("acme.com"),
            )
            return role, pages

        role, pages = asyncio.run(run())
        assert role == "careers@acme.com"
        assert "careers@acme.com" in pages
        # The shared, in-flight-deduped cache means /careers is fetched exactly
        # once even though two scanners requested it concurrently.
        careers_hits = hits.get("https://acme.com/careers", 0)
        assert careers_hits == 1, f"/careers fetched {careers_hits}x, expected 1 (cache/inflight dedup failed)"

    def test_owner_cancellation_hands_piggybackers_a_miss(self):
        """A hunt's resolve budget expiring cancels its fetch tasks mid-flight.
        A task from ANOTHER hunt piggybacking on the same URL must receive a
        plain miss ("") — never a CancelledError, which bypasses `except
        Exception` guards and would tear down a healthy scan."""
        import asyncio
        import httpx
        from app.scrapers import web as web_mod

        async def run():
            started = asyncio.Event()

            async def handler(request: httpx.Request) -> httpx.Response:
                started.set()
                await asyncio.sleep(30)   # hold the fetch open until cancelled
                return httpx.Response(200, text="never reached")

            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            url = "https://acme.com/careers"
            owner = asyncio.create_task(web_mod._cached_get(client, url, 4))
            await started.wait()
            piggy = asyncio.create_task(web_mod._cached_get(client, url, 4))
            await asyncio.sleep(0.01)     # let piggy park on the shared future
            owner.cancel()
            result = await asyncio.wait_for(piggy, 5)
            assert result == ""           # a miss — not an exception
            assert owner.cancelled()
            # Nothing cached: the URL stays immediately retryable.
            assert url not in web_mod._page_cache
            await client.aclose()

        asyncio.run(run())

    def test_piggybacker_cancellation_leaves_owner_and_others_intact(self):
        """One hunt giving up must not cancel the SHARED future other hunts
        are awaiting (the un-shielded-await bug: Task.cancel() cancels the
        future the task is parked on)."""
        import asyncio
        import httpx
        from app.scrapers import web as web_mod

        async def run():
            started = asyncio.Event()
            release = asyncio.Event()

            async def handler(request: httpx.Request) -> httpx.Response:
                started.set()
                await release.wait()
                return httpx.Response(200, text="reach us at careers@acme.com")

            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            url = "https://acme.com/careers"
            owner  = asyncio.create_task(web_mod._cached_get(client, url, 4))
            await started.wait()
            piggy1 = asyncio.create_task(web_mod._cached_get(client, url, 4))
            piggy2 = asyncio.create_task(web_mod._cached_get(client, url, 4))
            await asyncio.sleep(0.01)
            piggy1.cancel()               # one hunt gives up…
            await asyncio.sleep(0.01)
            release.set()                 # …the fetch still completes normally
            assert await asyncio.wait_for(owner, 5)  == "reach us at careers@acme.com"
            assert await asyncio.wait_for(piggy2, 5) == "reach us at careers@acme.com"
            assert piggy1.cancelled()
            await client.aclose()

        asyncio.run(run())

    def test_short_timeout_miss_does_not_bind_longer_timeout_caller(self):
        """A page that failed under the careers scan's 4s budget must stay
        reachable to the 8s page scan (pre-cache behavior for slow pages),
        while same-budget callers reuse the miss within its short TTL."""
        import asyncio
        import httpx
        from app.scrapers import web as web_mod

        async def run():
            failing = httpx.AsyncClient(
                transport=httpx.MockTransport(lambda r: httpx.Response(503)))
            working = httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda r: httpx.Response(200, text="hi careers@acme.com")))
            url = "https://acme.com/careers"
            assert await web_mod._cached_get(failing, url, 4) == ""   # miss @4s
            # Same budget inside the negative TTL: miss is reused, no refetch.
            assert await web_mod._cached_get(working, url, 4) == ""
            # Longer budget: the miss doesn't bind — refetch succeeds.
            assert "careers@acme.com" in await web_mod._cached_get(working, url, 8)
            await failing.aclose()
            await working.aclose()

        asyncio.run(run())


class TestWorkdayScraper:
    """WorkdayScraper reads the public Workday CXS JSON API and emits
    identity-only domain leads (Workday postings never carry an email). The
    company→domain mapping comes from the curated registry, never the API."""

    @staticmethod
    def _tenant(company, tenant, domain):
        from app.scrapers.workday import WorkdayTenant
        return WorkdayTenant(company, tenant, "wd1", "Site", domain)

    @staticmethod
    def _install(monkeypatch, tenants, responder, hits=None):
        """Point WorkdayScraper at a fixed registry and a MockTransport whose
        `responder(tenant, search_text)` returns the CXS JSON (or an
        (status, body) tuple / raw string) for each probed tenant."""
        import json
        import httpx
        from app.scrapers import workday as workday_mod

        monkeypatch.setattr(workday_mod, "_TENANTS", list(tenants))

        def handler(request: httpx.Request) -> httpx.Response:
            tenant = request.url.path.split("/")[3]   # /wday/cxs/{tenant}/{site}/jobs
            try:
                search_text = json.loads(request.content).get("searchText", "")
            except Exception:
                search_text = ""
            if hits is not None:
                hits.append(tenant)
            out = responder(tenant, search_text)
            if isinstance(out, tuple):                       # (status, json-body)
                return httpx.Response(out[0], json=out[1])
            if isinstance(out, str):                         # raw (possibly malformed) text
                return httpx.Response(200, text=out)
            return httpx.Response(200, json=out)             # dict → 200 JSON

        real_client = httpx.AsyncClient

        def fake_async_client(*a, **kw):
            for k in ("timeout", "headers", "limits"):
                kw.pop(k, None)
            return real_client(transport=httpx.MockTransport(handler))

        monkeypatch.setattr(workday_mod.httpx, "AsyncClient", fake_async_client)

    @staticmethod
    def _jobs(*titles, location="Remote"):
        return {"total": len(titles), "jobPostings": [
            {"title": t, "externalPath": f"/job/x/{i}", "locationsText": location,
             "postedOn": "Posted Today", "bulletFields": [f"JR{i}"]}
            for i, t in enumerate(titles)
        ]}

    def test_role_query_emits_leads_for_matching_titles_and_dedupes_by_domain(self, monkeypatch):
        import asyncio
        from app.scrapers.workday import WorkdayScraper

        # Two tenants SHARE a domain (acme.com) — the lead must be deduped to one.
        # Delta returns no React role and must be dropped by the role filter.
        tenants = [
            self._tenant("Acme", "acme", "acme.com"),
            self._tenant("Acme EU", "acmeeu", "acme.com"),
            self._tenant("Gamma", "gamma", "gamma.com"),
            self._tenant("Delta", "delta", "delta.com"),
        ]

        def responder(tenant, search_text):
            if tenant == "delta":
                return self._jobs("Marketing Coordinator", "Sales Manager")
            return self._jobs("Senior React Engineer", "Backend Engineer")

        self._install(monkeypatch, tenants, responder)

        leads = asyncio.run(WorkdayScraper().safe_search("react engineer"))
        domains = sorted(l["_domain"] for l in leads)
        assert domains == ["acme.com", "gamma.com"]          # acmeeu deduped, delta filtered
        lead = next(l for l in leads if l["_domain"] == "acme.com")
        assert lead["email"] == "" and lead["name"] == ""    # identity-only
        assert lead["designation"] == "Recruiter"
        assert lead["source"].startswith("Workday/")
        assert "React" in lead["context"]

    def test_company_query_probes_only_the_registry_tenant(self, monkeypatch):
        import asyncio
        from app.scrapers.workday import WorkdayScraper

        tenants = [
            self._tenant("Visa", "visa", "visa.com"),
            self._tenant("Mastercard", "mastercard", "mastercard.com"),
        ]
        hits: list[str] = []

        def responder(tenant, search_text):
            return self._jobs("Staff Software Engineer", "Data Scientist")

        self._install(monkeypatch, tenants, responder, hits=hits)

        leads = asyncio.run(WorkdayScraper().safe_search("Visa"))
        assert hits == ["visa"]                              # ONLY the matched tenant probed
        assert [l["_domain"] for l in leads] == ["visa.com"]
        assert leads[0]["company"] == "Visa"

    def test_non_registry_company_returns_empty_without_probing(self, monkeypatch):
        import asyncio
        from app.scrapers.workday import WorkdayScraper

        tenants = [self._tenant("Visa", "visa", "visa.com")]
        hits: list[str] = []

        def responder(tenant, search_text):
            return self._jobs("Engineer")

        self._install(monkeypatch, tenants, responder, hits=hits)

        leads = asyncio.run(WorkdayScraper().safe_search("Definitelynotarealcompany"))
        assert leads == []
        assert hits == []                                    # blind discovery is skipped

    def test_404_or_422_tenant_is_skipped_gracefully(self, monkeypatch):
        import asyncio
        from app.scrapers.workday import WorkdayScraper

        tenants = [
            self._tenant("GoodCo", "goodco", "goodco.com"),
            self._tenant("GoneCo", "goneco", "goneco.com"),
            self._tenant("BadCo", "badco", "badco.com"),
        ]

        def responder(tenant, search_text):
            if tenant == "goneco":
                return (404, {"error": "gone"})
            if tenant == "badco":
                return (422, {"error": "unprocessable"})
            return self._jobs("Software Engineer")

        self._install(monkeypatch, tenants, responder)

        leads = asyncio.run(WorkdayScraper().safe_search("engineer"))
        assert [l["_domain"] for l in leads] == ["goodco.com"]

    def test_malformed_json_does_not_crash(self, monkeypatch):
        import asyncio
        from app.scrapers.workday import WorkdayScraper

        tenants = [self._tenant("Acme", "acme", "acme.com")]

        def responder(tenant, search_text):
            return "this is <not> json {{{ 200 OK"          # raw text, 200 status

        self._install(monkeypatch, tenants, responder)

        leads = asyncio.run(WorkdayScraper().safe_search("engineer"))
        assert leads == []                                   # swallowed, no exception


class TestInboxReplies:
    """Reply-content persistence on sync + GET /api/inbox/replies (v2 inbox)."""

    @staticmethod
    def _install_fake_imap(monkeypatch, raw_messages: list[bytes]):
        """Fake imaplib.IMAP4_SSL serving the given raw RFC822 messages. The
        same bytes answer both the header-fields fetch and the full-body fetch —
        the parser just sees extra headers either way."""
        from app.api import inbox as inbox_mod

        class FakeIMAP:
            def __init__(self, host):
                pass
            def login(self, addr, pw):
                return "OK", []
            def select(self, mailbox, readonly=False):
                return "OK", []
            def search(self, charset, query):
                uids = " ".join(str(i + 1) for i in range(len(raw_messages)))
                return "OK", [uids.encode()]
            def fetch(self, uid, what):
                return "OK", [(b"1 (BODY[])", raw_messages[int(uid) - 1])]
            def logout(self):
                return "OK", []

        monkeypatch.setattr(inbox_mod.imaplib, "IMAP4_SSL", FakeIMAP)

    @staticmethod
    def _raw_reply(from_addr: str, subject: str, body: str,
                   date: str = "Sat, 18 Jul 2026 10:30:00 +0000") -> bytes:
        return (
            f"From: Priya Sharma <{from_addr}>\r\nDate: {date}\r\n"
            f"Subject: {subject}\r\nContent-Type: text/plain\r\n\r\n{body}"
        ).encode()

    def _seed_awaiting_contact(self, auth_client, email="priya@startup.com"):
        contact = auth_client.post("/api/contacts", json={
            "name": "Priya Sharma", "email": email,
            "designation": "Technical Recruiter", "company": "StartupCo",
        }).json()
        r = auth_client.patch(f"/api/contacts/{contact['id']}", json={
            "status": "emailed", "last_emailed_at": "2026-07-10T09:00:00",
        })
        assert r.status_code == 200
        return contact

    _SYNC_CREDS = {"gmail_address": "me@gmail.com", "gmail_app_password": "app-pw"}

    def test_sync_persists_reply_message_and_dedupes_on_resync(self, auth_client, monkeypatch):
        contact = self._seed_awaiting_contact(auth_client)
        body = "Thanks for reaching out!\r\n\r\nLet's talk.   " + "word " * 120
        self._install_fake_imap(monkeypatch, [
            self._raw_reply("priya@startup.com", "Re: Backend role", body),
        ])

        r = auth_client.post("/api/inbox/sync", json=self._SYNC_CREDS)
        assert r.status_code == 200, r.text
        assert r.json()["replies_found"] == 1

        replies = auth_client.get("/api/inbox/replies").json()
        assert len(replies) == 1
        rep = replies[0]
        assert rep["contact_id"] == contact["id"]
        assert rep["name"] == "Priya Sharma"
        assert rep["company"] == "StartupCo"
        assert rep["designation"] == "Technical Recruiter"
        assert rep["status"] == "replied"          # sync flipped the contact
        assert rep["subject"] == "Re: Backend role"
        # Snippet is whitespace-normalized and capped at ~400 chars.
        assert rep["snippet"].startswith("Thanks for reaching out! Let's talk. word")
        assert len(rep["snippet"]) <= 400
        assert rep["received_at"].startswith("2026-07-18T10:30:00")

        # Re-sync the SAME message (reset the contact so it's awaiting again):
        # the contact re-flips, but no duplicate ReplyMessage row appears.
        auth_client.patch(f"/api/contacts/{contact['id']}", json={
            "status": "emailed", "replied_at": None,
            "last_emailed_at": "2026-07-10T09:00:00",
        })
        r = auth_client.post("/api/inbox/sync", json=self._SYNC_CREDS)
        assert r.json()["replies_found"] == 1
        assert len(auth_client.get("/api/inbox/replies").json()) == 1

    def test_replies_endpoint_is_user_scoped(self, auth_client, db_session):
        from datetime import datetime
        from app.db.models import Contact, ReplyMessage, User

        me = db_session.query(User).first()
        other = User(email="other@example.com", password_hash="x")
        db_session.add(other)
        db_session.commit()

        mine   = Contact(user_id=me.id,    name="Mine",   email="mine@a.com",
                         designation="Recruiter", company="A", status="replied")
        theirs = Contact(user_id=other.id, name="Theirs", email="theirs@b.com",
                         designation="Recruiter", company="B", status="replied")
        db_session.add_all([mine, theirs])
        db_session.commit()
        db_session.add_all([
            ReplyMessage(user_id=me.id,    contact_id=mine.id,   subject="mine",
                         snippet="hi", received_at=datetime(2026, 7, 17, 10, 0)),
            ReplyMessage(user_id=other.id, contact_id=theirs.id, subject="theirs",
                         snippet="hi", received_at=datetime(2026, 7, 18, 10, 0)),
        ])
        db_session.commit()

        replies = auth_client.get("/api/inbox/replies").json()
        assert [r["subject"] for r in replies] == ["mine"]

    def test_replies_endpoint_newest_first(self, auth_client, db_session):
        from datetime import datetime
        from app.db.models import Contact, ReplyMessage, User

        me = db_session.query(User).first()
        c = Contact(user_id=me.id, name="P", email="p@a.com",
                    designation="Recruiter", company="A", status="replied")
        db_session.add(c)
        db_session.commit()
        db_session.add_all([
            ReplyMessage(user_id=me.id, contact_id=c.id, subject="older",
                         snippet="", received_at=datetime(2026, 7, 10, 9, 0)),
            ReplyMessage(user_id=me.id, contact_id=c.id, subject="newer",
                         snippet="", received_at=datetime(2026, 7, 18, 9, 0)),
        ])
        db_session.commit()

        replies = auth_client.get("/api/inbox/replies").json()
        assert [r["subject"] for r in replies] == ["newer", "older"]


class TestAnalyticsSummary:
    """GET /api/analytics/summary — computed on read from contacts rows."""

    def _seed(self, db_session):
        """Deterministic contacts anchored on the current ISO week: one sent +
        replied this week, two sent last week (one replied, one not), and one
        never-emailed row that must count nowhere."""
        from datetime import datetime, time, timedelta
        from app.db.models import Contact, User
        from app.timeutil import utcnow

        monday = utcnow().date() - timedelta(days=utcnow().date().weekday())
        def at(week_monday, day, hour):
            return datetime.combine(week_monday + timedelta(days=day), time(hour, 0))
        last_monday = monday - timedelta(days=7)

        user = db_session.query(User).first()
        db_session.add_all([
            # This week, Tue 09:00 (morning) — replied same day.
            Contact(user_id=user.id, name="R", email="r@a.com", company="A",
                    designation="Technical Recruiter", status="replied",
                    last_emailed_at=at(monday, 1, 9), replied_at=at(monday, 1, 15)),
            # Last week, Wed 14:00 (afternoon) — no reply.
            Contact(user_id=user.id, name="E", email="e@b.com", company="B",
                    designation="Software Engineer", status="emailed",
                    last_emailed_at=at(last_monday, 2, 14)),
            # Last week, Wed 19:00 (evening) — replied next day, now interviewing.
            Contact(user_id=user.id, name="F", email="f@c.com", company="C",
                    designation="Founder", status="interview",
                    last_emailed_at=at(last_monday, 2, 19),
                    replied_at=at(last_monday, 3, 10)),
            # Never emailed — excluded from every metric.
            Contact(user_id=user.id, name="N", email="n@d.com", company="D",
                    designation="CTO", status="new"),
        ])
        db_session.commit()
        return monday

    def test_summary_with_seeded_contacts(self, auth_client, db_session):
        monday = self._seed(db_session)

        r = auth_client.get("/api/analytics/summary")
        assert r.status_code == 200
        data = r.json()

        # Weekly: 6 ISO weeks, oldest → current, Monday-keyed.
        weekly = data["weekly"]
        assert len(weekly) == 6
        assert weekly[-1]["week_start"] == monday.isoformat()
        assert (weekly[-1]["sent"], weekly[-1]["replied"], weekly[-1]["rate"]) == (1, 1, 1.0)
        assert (weekly[-2]["sent"], weekly[-2]["replied"], weekly[-2]["rate"]) == (2, 1, 0.5)
        assert all(w["sent"] == 0 and w["replied"] == 0 for w in weekly[:4])

        # Send-time histogram: weekday (0=Mon..6=Sun) × day-part.
        cells = {(c["weekday"], c["part"]): c for c in data["send_time"]}
        assert len(cells) == 21
        assert (cells[(1, "morning")]["sent"],   cells[(1, "morning")]["replied"])   == (1, 1)
        assert (cells[(2, "afternoon")]["sent"], cells[(2, "afternoon")]["replied"]) == (1, 0)
        assert (cells[(2, "evening")]["sent"],   cells[(2, "evening")]["replied"])   == (1, 1)
        assert sum(c["sent"] for c in data["send_time"]) == 3   # never-emailed excluded

        # By-role: hunt.py's family classifier, rate-desc, only sent > 0.
        by_role = {r["family"]: r for r in data["by_role"]}
        assert set(by_role) == {"recruiting", "engineering", "founder_exec"}
        assert by_role["recruiting"]["rate"] == 1.0
        assert by_role["founder_exec"]["rate"] == 1.0
        assert (by_role["engineering"]["sent"], by_role["engineering"]["rate"]) == (1, 0.0)
        assert data["by_role"][-1]["family"] == "engineering"   # lowest rate last

        assert data["totals"] == {
            "sent": 3, "replied": 2, "interviews": 1, "offers": 0,
            "reply_rate": round(2 / 3, 3),
        }

    def test_summary_empty_state(self, auth_client):
        """Fresh user: all zeros, no division errors, stable shapes."""
        data = auth_client.get("/api/analytics/summary").json()
        assert data["totals"] == {"sent": 0, "replied": 0, "interviews": 0,
                                  "offers": 0, "reply_rate": 0.0}
        assert len(data["weekly"]) == 6
        assert all(w["sent"] == 0 and w["rate"] == 0.0 for w in data["weekly"])
        assert data["by_role"] == []
        assert len(data["send_time"]) == 21
        assert all(c["sent"] == 0 for c in data["send_time"])

    def test_requires_auth(self, client):
        assert client.get("/api/analytics/summary").status_code in (401, 403)


class TestGmailOAuthSend:
    """POST /api/send/bulk over a stored OAuth grant (Gmail REST API path)."""

    @staticmethod
    def _store_oauth_grant(db_session, address="oauth-user@gmail.com",
                           refresh_token="refresh-123"):
        from app.db.models import User
        from app.db.crud import ConfigRepository
        user = db_session.query(User).first()
        cfg = ConfigRepository(db_session, user.id)
        cfg.set("gmail_oauth_address", address)
        cfg.set("gmail_oauth_refresh_token", refresh_token)
        return user

    @staticmethod
    def _seed_drafted_contact(db_session, user_id, email):
        from app.db.models import Contact, EmailDraft
        c = Contact(user_id=user_id, name="Lead Person", email=email,
                    designation="Software Engineer", company="StartupCo")
        db_session.add(c)
        db_session.commit()
        db_session.add(EmailDraft(user_id=user_id, contact_id=c.id,
                                  subject="Quick question", body="Hello there"))
        db_session.commit()
        return c

    @staticmethod
    def _forbid_smtp(monkeypatch):
        """The OAuth path must never open an SMTP session."""
        from app.api import send as send_mod

        def _boom(*a, **k):
            raise AssertionError("SMTP was used on the OAuth path")
        monkeypatch.setattr(send_mod.smtplib, "SMTP", _boom)

    def test_oauth_send_happy_path(self, auth_client, db_session, monkeypatch):
        from app import gmail_oauth
        from app.api import send as send_mod
        user = self._store_oauth_grant(db_session)
        self._seed_drafted_contact(db_session, user.id, "a@startup.com")
        self._seed_drafted_contact(db_session, user.id, "b@other.com")

        monkeypatch.setattr(send_mod.time, "sleep", lambda *_: None)
        self._forbid_smtp(monkeypatch)
        refreshed_with = []
        monkeypatch.setattr(gmail_oauth, "access_token_for",
                            lambda rt: refreshed_with.append(rt) or "access-tok")
        sent_msgs = []

        def fake_send_raw(token, mime_bytes):
            assert token == "access-tok"
            sent_msgs.append(mime_bytes)
        monkeypatch.setattr(gmail_oauth, "send_raw", fake_send_raw)

        r = auth_client.post("/api/send/bulk", json={})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["sent"] == 2 and data["failed"] == 0

        # One Gmail API send per queued contact, from the connected address.
        assert len(sent_msgs) == 2
        assert refreshed_with == ["refresh-123"]
        assert all(b"oauth-user@gmail.com" in m for m in sent_msgs)

        statuses = {c["email"]: c["status"] for c in auth_client.get("/api/contacts").json()}
        assert statuses["a@startup.com"] == "emailed"
        assert statuses["b@other.com"] == "emailed"

    def test_grant_revoked_returns_400_with_reconnect(self, auth_client, db_session, monkeypatch):
        from app import gmail_oauth
        user = self._store_oauth_grant(db_session)
        self._seed_drafted_contact(db_session, user.id, "a@startup.com")

        def _revoked(rt):
            raise gmail_oauth.GrantRevoked()
        monkeypatch.setattr(gmail_oauth, "access_token_for", _revoked)

        r = auth_client.post("/api/send/bulk", json={})
        # 400, not 401 — 401 would force-log-out the frontend session.
        assert r.status_code == 400, r.text
        assert "reconnect" in r.json()["detail"].lower()

        # Nothing was sent — the contact is still untouched.
        statuses = {c["email"]: c["status"] for c in auth_client.get("/api/contacts").json()}
        assert statuses["a@startup.com"] == "new"

    def test_oauth_preferred_over_stored_app_password(self, auth_client, db_session, monkeypatch):
        from app import gmail_oauth
        from app.api import send as send_mod
        from app.db.crud import ConfigRepository
        user = self._store_oauth_grant(db_session)
        # Both methods stored — OAuth must win (and SMTP never be touched).
        cfg = ConfigRepository(db_session, user.id)
        cfg.set("gmail_address", "legacy@gmail.com")
        cfg.set("gmail_app_password", "abcdabcdabcdabcd")
        self._seed_drafted_contact(db_session, user.id, "a@startup.com")

        monkeypatch.setattr(send_mod.time, "sleep", lambda *_: None)
        self._forbid_smtp(monkeypatch)
        monkeypatch.setattr(gmail_oauth, "access_token_for", lambda rt: "access-tok")
        sent_msgs = []
        monkeypatch.setattr(gmail_oauth, "send_raw",
                            lambda tok, mime: sent_msgs.append(mime))

        r = auth_client.post("/api/send/bulk", json={})
        assert r.status_code == 200, r.text
        assert r.json()["sent"] == 1
        assert len(sent_msgs) == 1
        # From-address is the OAuth-connected account, not the App Password one.
        assert b"oauth-user@gmail.com" in sent_msgs[0]

    def test_grant_revoked_mid_batch_fails_remaining(self, auth_client, db_session, monkeypatch):
        from app import gmail_oauth
        from app.api import send as send_mod
        user = self._store_oauth_grant(db_session)
        for i in range(3):
            self._seed_drafted_contact(db_session, user.id, f"lead{i}@startup.com")

        monkeypatch.setattr(send_mod.time, "sleep", lambda *_: None)
        self._forbid_smtp(monkeypatch)
        monkeypatch.setattr(gmail_oauth, "access_token_for", lambda rt: "access-tok")
        calls = []

        def flaky_send_raw(token, mime):
            calls.append(mime)
            if len(calls) >= 2:                    # token dies after the first send
                raise gmail_oauth.GrantRevoked()
        monkeypatch.setattr(gmail_oauth, "send_raw", flaky_send_raw)

        r = auth_client.post("/api/send/bulk", json={})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["sent"] == 1 and data["failed"] == 2
        # After the revocation, no further Gmail API calls are attempted.
        assert len(calls) == 2
        failures = [x for x in data["results"] if x["status"] == "failed"]
        assert all("reconnect" in x["error"] for x in failures)


class TestGmailOAuthInboxSync:
    """POST /api/inbox/sync over a stored OAuth grant (Gmail REST API path)."""

    def _seed_awaiting_contact(self, auth_client, email="priya@startup.com"):
        contact = auth_client.post("/api/contacts", json={
            "name": "Priya Sharma", "email": email,
            "designation": "Technical Recruiter", "company": "StartupCo",
        }).json()
        r = auth_client.patch(f"/api/contacts/{contact['id']}", json={
            "status": "emailed", "last_emailed_at": "2026-07-10T09:00:00",
        })
        assert r.status_code == 200
        return contact

    @staticmethod
    def _forbid_imap(monkeypatch):
        from app.api import inbox as inbox_mod

        def _boom(*a, **k):
            raise AssertionError("IMAP was used on the OAuth path")
        monkeypatch.setattr(inbox_mod.imaplib, "IMAP4_SSL", _boom)

    def test_sync_via_oauth_persists_reply_and_flips_status(self, auth_client, db_session, monkeypatch):
        from datetime import datetime, timezone
        from app import gmail_oauth
        contact = self._seed_awaiting_contact(auth_client)
        TestGmailOAuthSend._store_oauth_grant(db_session)

        self._forbid_imap(monkeypatch)
        monkeypatch.setattr(gmail_oauth, "access_token_for", lambda rt: "access-tok")
        calls = []

        def fake_find(token, sender, after_epoch):
            assert token == "access-tok"
            calls.append((sender, after_epoch))
            return [
                {"subject": "Re: Backend role", "snippet": "Thanks for reaching out!",
                 "received_at": datetime(2026, 7, 18, 10, 30)},
                {"subject": "Re: Backend role", "snippet": "Bumping this thread",
                 "received_at": datetime(2026, 7, 19, 8, 0)},
            ]
        monkeypatch.setattr(gmail_oauth, "find_replies_from", fake_find)

        r = auth_client.post("/api/inbox/sync", json={})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["scanned"] == 1
        assert data["replies_found"] == 1
        assert data["bounces_found"] == 0
        assert data["hits"][0]["email"] == "priya@startup.com"

        # Cutoff mirrors the IMAP semantics: the query goes back exactly to
        # when we emailed this contact (naive UTC treated as UTC).
        expected_epoch = int(datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc).timestamp())
        assert calls == [("priya@startup.com", expected_epoch)]

        # Status flipped and the EARLIEST message was persisted for the inbox.
        replies = auth_client.get("/api/inbox/replies").json()
        assert len(replies) == 1
        rep = replies[0]
        assert rep["contact_id"] == contact["id"]
        assert rep["status"] == "replied"
        assert rep["subject"] == "Re: Backend role"
        assert rep["snippet"] == "Thanks for reaching out!"
        assert rep["received_at"].startswith("2026-07-18T10:30:00")

    def test_sync_grant_revoked_returns_400_with_reconnect(self, auth_client, db_session, monkeypatch):
        from app import gmail_oauth
        self._seed_awaiting_contact(auth_client)
        TestGmailOAuthSend._store_oauth_grant(db_session)
        self._forbid_imap(monkeypatch)

        def _revoked(rt):
            raise gmail_oauth.GrantRevoked()
        monkeypatch.setattr(gmail_oauth, "access_token_for", _revoked)

        r = auth_client.post("/api/inbox/sync", json={})
        assert r.status_code == 400, r.text
        assert "reconnect" in r.json()["detail"].lower()

    def test_explicit_request_creds_still_use_imap(self, auth_client, db_session, monkeypatch):
        """Explicit App Password creds in the request outrank the stored grant."""
        from app import gmail_oauth
        self._seed_awaiting_contact(auth_client)
        TestGmailOAuthSend._store_oauth_grant(db_session)

        def _no_oauth(rt):
            raise AssertionError("OAuth used despite explicit request creds")
        monkeypatch.setattr(gmail_oauth, "access_token_for", _no_oauth)
        TestInboxReplies._install_fake_imap(monkeypatch, [
            TestInboxReplies._raw_reply("priya@startup.com", "Re: hi", "yo"),
        ])

        r = auth_client.post("/api/inbox/sync", json={
            "gmail_address": "me@gmail.com", "gmail_app_password": "app-pw",
        })
        assert r.status_code == 200, r.text
        assert r.json()["replies_found"] == 1


class TestGmailOAuthEndpoints:
    """GET /api/config/gmail/oauth/start + the unauthenticated callback."""

    def test_start_returns_consent_url(self, auth_client, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "google_client_id", "client-id-123")
        monkeypatch.setattr(settings, "google_client_secret", "client-secret")
        r = auth_client.get("/api/config/gmail/oauth/start")
        assert r.status_code == 200, r.text
        url = r.json()["url"]
        assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
        assert "client_id=client-id-123" in url
        assert "state=" in url

    def test_start_503_when_not_configured(self, auth_client, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "google_client_id", "")
        monkeypatch.setattr(settings, "google_client_secret", "")
        assert auth_client.get("/api/config/gmail/oauth/start").status_code == 503

    def test_callback_forged_state_redirects_error(self, client, monkeypatch):
        from app import gmail_oauth

        def _never(code):
            raise AssertionError("exchange_code called with a forged state")
        monkeypatch.setattr(gmail_oauth, "exchange_code", _never)

        r = client.get("/api/config/gmail/oauth/callback",
                       params={"state": "forged-garbage", "code": "abc"},
                       follow_redirects=False)
        assert r.status_code in (302, 307)
        assert "gmail=error" in r.headers["location"]

    def test_callback_expired_state_redirects_error(self, client, monkeypatch):
        import json
        from datetime import datetime, timezone, timedelta
        from app import security, gmail_oauth

        def _never(code):
            raise AssertionError("exchange_code called with an expired state")
        monkeypatch.setattr(gmail_oauth, "exchange_code", _never)

        # Genuine (decryptable) state whose TTL has passed.
        payload = {"purpose": "gmail-oauth", "uid": 1,
                   "exp": (datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp()}
        state = security._fernet.encrypt(json.dumps(payload).encode()).decode()

        r = client.get("/api/config/gmail/oauth/callback",
                       params={"state": state, "code": "abc"},
                       follow_redirects=False)
        assert "gmail=error" in r.headers["location"]

    def test_callback_happy_path_stores_encrypted_refresh_token(self, auth_client, db_session, monkeypatch):
        from app import gmail_oauth
        from app.db.models import User, AppConfig
        from app.db.crud import ConfigRepository

        user = db_session.query(User).first()
        state = gmail_oauth.make_state(user.id)
        monkeypatch.setattr(gmail_oauth, "exchange_code",
                            lambda code: ("refresh-xyz", "me@gmail.com"))

        r = auth_client.get("/api/config/gmail/oauth/callback",
                            params={"state": state, "code": "good-code"},
                            follow_redirects=False)
        assert r.status_code in (302, 307)
        assert "gmail=connected" in r.headers["location"]

        db_session.commit()   # end the read transaction so the write is visible
        addr, token = ConfigRepository(db_session, user.id).get_gmail_oauth()
        assert (addr, token) == ("me@gmail.com", "refresh-xyz")
        # The refresh token never touches the DB in cleartext.
        raw = db_session.query(AppConfig).filter_by(
            user_id=user.id, key="gmail_oauth_refresh_token").first()
        assert raw.value and raw.value != "refresh-xyz"

        # The connection now reports as OAuth-connected.
        status = auth_client.get("/api/config").json()
        assert status["has_gmail"] is True
        assert status["gmail_method"] == "oauth"
        assert status["gmail_address"] == "me@gmail.com"


class TestHackerNewsScraper:
    """The HN 'Who is hiring' source (Algolia API) — highest-yield free source.
    All tests mock the two Algolia calls so they're deterministic and offline."""

    def _mock(self, monkeypatch, comments):
        import httpx
        from app.scrapers import hackernews as hn
        hn._cache.update(at=0.0, posts=[])   # bypass the per-process thread cache

        def handler(request: httpx.Request) -> httpx.Response:
            if "author_whoishiring" in str(request.url):
                return httpx.Response(200, json={"hits": [
                    {"objectID": "999", "title": "Ask HN: Who is hiring? (July 2026)"},
                    {"objectID": "998", "title": "Ask HN: Who wants to be hired? (July 2026)"},
                ]})
            return httpx.Response(200, json={
                "hits": [{"comment_text": c} for c in comments],
                "nbPages": 1,
            })

        real = httpx.AsyncClient
        def fake(*a, **kw):
            for k in ("timeout", "headers", "follow_redirects"):
                kw.pop(k, None)
            return real(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(hn.httpx, "AsyncClient", fake)

    def test_embedded_email_becomes_direct_lead(self, monkeypatch):
        import asyncio
        from app.scrapers.hackernews import HackerNewsScraper
        self._mock(monkeypatch, [
            "Acme Corp | Senior React Engineer | Remote | We use React and Go. Apply: jobs@acme.com",
        ])
        leads = asyncio.run(HackerNewsScraper().safe_search("react engineer"))
        assert len(leads) == 1
        assert leads[0]["email"] == "jobs@acme.com"
        assert leads[0]["company"] == "Acme Corp"
        assert leads[0]["source"] == "HackerNews"

    def test_no_email_emits_domain_lead_from_url(self, monkeypatch):
        import asyncio
        from app.scrapers.hackernews import HackerNewsScraper
        self._mock(monkeypatch, [
            "Beta Labs | Backend Engineer (Python) | apply at https://beta-labs.io/careers",
        ])
        leads = asyncio.run(HackerNewsScraper().safe_search("python backend"))
        assert len(leads) == 1
        assert leads[0]["email"] == ""
        assert leads[0]["_domain"] == "beta-labs.io"

    def test_role_filter_drops_off_target_posts(self, monkeypatch):
        import asyncio
        from app.scrapers.hackernews import HackerNewsScraper
        self._mock(monkeypatch, [
            "Acme | Senior React Engineer | react@acme.com",
            "Widget Co | Sales Manager | sales@widget.com",   # off-target
        ])
        leads = asyncio.run(HackerNewsScraper().safe_search("react engineer"))
        assert [l["email"] for l in leads] == ["react@acme.com"]

    def test_aggregator_domains_are_dropped(self, monkeypatch):
        import asyncio
        from app.scrapers.hackernews import HackerNewsScraper
        self._mock(monkeypatch, [
            # greenhouse link + no real employer domain/email → no lead
            "Ghost Inc | React Engineer | https://boards.greenhouse.io/ghost/jobs/123",
        ])
        leads = asyncio.run(HackerNewsScraper().safe_search("react engineer"))
        assert leads == []

    def test_seeker_post_is_never_emitted(self, monkeypatch):
        import asyncio
        from app.scrapers.hackernews import HackerNewsScraper
        self._mock(monkeypatch, [
            "Jane Dev | Senior React Engineer seeking remote work | jane@gmail.com",
        ])
        leads = asyncio.run(HackerNewsScraper().safe_search("react engineer"))
        assert leads == []   # 'seeking' guard + gmail is an aggregator anyway


class TestWorkingNomadsScraper:
    """WorkingNomads JSON board — its `tags` field is a comma-joined STRING,
    unlike the list-typed boards, so the parsing needs its own coverage."""

    def _mock(self, monkeypatch, jobs):
        import httpx
        from app.scrapers import jobboards as jb

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=jobs)

        real = httpx.AsyncClient
        def fake(*a, **kw):
            for k in ("timeout", "headers", "follow_redirects"):
                kw.pop(k, None)
            return real(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(jb.httpx, "AsyncClient", fake)

    def test_role_query_matches_via_comma_string_tags(self, monkeypatch):
        import asyncio
        from app.scrapers.jobboards import WorkingNomadsScraper
        self._mock(monkeypatch, [
            {"title": "Senior AI Engineer", "company_name": "Lemon.io",
             "tags": "python,machine learning,react", "category_name": "Development",
             "description": "Build things."},
            {"title": "Account Executive", "company_name": "SalesCo",
             "tags": "sales,crm", "category_name": "Sales", "description": "Sell things."},
        ])
        leads = asyncio.run(WorkingNomadsScraper().safe_search("react"))
        assert len(leads) == 1
        assert leads[0]["_domain"] == "lemon.io"
        assert leads[0]["company"] == "Lemon.io"

    def test_missing_fields_do_not_crash(self, monkeypatch):
        import asyncio
        from app.scrapers.jobboards import WorkingNomadsScraper
        self._mock(monkeypatch, [{"title": "Engineer"}, "junk", {}])
        leads = asyncio.run(WorkingNomadsScraper().safe_search("engineer"))
        assert isinstance(leads, list)   # no company/domain → dropped, but no crash


class TestHuntReviewRegressions:
    """Locks in the fixes from the comprehensive hunt-pipeline review so they
    can't silently regress."""

    def test_web_search_grounding_never_fabricates_prefix_domain(self, monkeypatch):
        """BLOCKER fix: a published address at a LONGER domain (acme.com.au)
        must not be truncated into a fabricated address at the target (acme.com)."""
        import asyncio, httpx
        from app.scrapers import web as web_mod
        web_mod._ground_cache.clear()

        # DDG returns a page mentioning careers@acme.com.au — but nothing at acme.com
        def handler(request):
            return httpx.Response(200, text="Reach the team at careers@acme.com.au for roles.")
        real = httpx.AsyncClient
        def fake(*a, **kw):
            for k in ("timeout", "follow_redirects", "headers"): kw.pop(k, None)
            return real(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(web_mod.httpx, "AsyncClient", fake)
        monkeypatch.setattr(web_mod, "resolves_public", lambda d: True)

        got = asyncio.run(web_mod.search_role_email_on_web("acme.com", "Acme"))
        assert got is None, f"fabricated {got!r} at acme.com from an acme.com.au address"

    def test_company_matches_multiword_superset(self):
        from app.scrapers.directory import company_matches
        assert company_matches("Goldman Sachs", "Goldman Sachs Group") is True
        assert company_matches("New York Times", "The New York Times Company") is True
        # word-aware guard still holds
        assert company_matches("visa", "Provisa") is False
        assert company_matches("stripe", "Striped") is False

    def test_slug_to_domain_keeps_bare_word_endings(self):
        from app.scrapers.ats import _slug_to_domain
        assert _slug_to_domain("twilio") == "twilio.com"      # not twil.com
        assert _slug_to_domain("openai") == "openai.com"      # not open.com
        assert _slug_to_domain("cisco") == "cisco.com"        # not cis.com
        # detachable ATS suffixes still stripped
        assert _slug_to_domain("twilio-inc") == "twilio.com"
        assert _slug_to_domain("acme-labs") == "acme.com"

    def test_hn_seeker_guard_keeps_employer_posts(self):
        from app.scrapers.hackernews import _SEEKER_RE
        # employer phrasing must NOT be flagged as a seeker
        assert not _SEEKER_RE.search("Acme | We are seeking a Senior Go Engineer | Remote")
        assert not _SEEKER_RE.search("Beta | Looking for a backend engineer to join us")
        # genuine seeker phrasing still caught
        assert _SEEKER_RE.search("I'm open to work, senior dev")
        assert _SEEKER_RE.search("Looking for a new role, remote preferred")

    def test_hn_aggregator_boundary_not_substring(self):
        from app.scrapers.hackernews import _is_agg
        assert _is_agg("x.com") is True
        assert _is_agg("netflix.com") is False     # was nuked by 'x.com' substring
        assert _is_agg("ashby-corp.com") is False   # was nuked by 'ashby' substring
        assert _is_agg("jobs.lever.co") is True      # subdomain boundary match

    def test_workday_lookup_no_wrong_company_crossmatch(self):
        from app.scrapers.workday import WorkdayScraper
        s = WorkdayScraper()
        # "Discovery" must NOT resolve to "Warner Bros Discovery"
        t = s._lookup("Discovery")
        assert t is None or "discovery" == t.company.lower()
        # exact identity still resolves
        assert s._lookup("Visa") is not None or s._lookup("visa") is not None


class TestHuntFixAuditCorrections:
    """Locks in the two regressions the fix-audit caught (fixes that were
    themselves buggy): the BLOCKER anchor over-blocked sentence-final
    addresses, and the role-inference gate broke standalone role queries."""

    def test_web_regex_matches_sentence_final_address(self, monkeypatch):
        """The anti-fabrication anchor must still MATCH a genuine published
        address that ends a sentence (trailing prose period)."""
        import asyncio, httpx
        from app.scrapers import web as web_mod
        web_mod._ground_cache.clear()

        def handler(request):
            # real published careers@acme.com ending a sentence
            return httpx.Response(200, text="For roles email careers@acme.com. Thanks!")
        real = httpx.AsyncClient
        def fake(*a, **kw):
            for k in ("timeout", "follow_redirects", "headers"): kw.pop(k, None)
            return real(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(web_mod.httpx, "AsyncClient", fake)
        monkeypatch.setattr(web_mod, "resolves_public", lambda d: True)

        got = asyncio.run(web_mod.search_role_email_on_web("acme.com", "Acme"))
        assert got == "careers@acme.com"          # matched despite trailing period
        web_mod._ground_cache.clear()

    def test_web_regex_still_blocks_longer_domain(self, monkeypatch):
        import asyncio, httpx
        from app.scrapers import web as web_mod
        web_mod._ground_cache.clear()
        def handler(request):
            return httpx.Response(200, text="Aussie careers@acme.com.au only.")
        real = httpx.AsyncClient
        def fake(*a, **kw):
            for k in ("timeout", "follow_redirects", "headers"): kw.pop(k, None)
            return real(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(web_mod.httpx, "AsyncClient", fake)
        monkeypatch.setattr(web_mod, "resolves_public", lambda d: True)
        assert asyncio.run(web_mod.search_role_email_on_web("acme.com", "Acme")) is None
        web_mod._ground_cache.clear()

    def test_standalone_role_queries_still_infer(self):
        from app.api.hunt import _resolve_target_role
        assert _resolve_target_role("", "engineering") == "engineering"
        assert _resolve_target_role("", "design") == "design"
        assert _resolve_target_role("", "recruiting") == "recruiting"

    def test_company_hunts_do_not_infer_but_explicit_wins(self):
        from app.api.hunt import _resolve_target_role
        assert _resolve_target_role("", "Stripe") == ""
        assert _resolve_target_role("", "Discovery") == ""
        assert _resolve_target_role("engineering", "Stripe") == "engineering"


class TestHunterInboxLabeling:
    """A Hunter generic-inbox result must be labelled by its prefix — a general
    inbox (contact@/hello@) gets the Company-Inbox template, a hiring inbox
    (careers@) the formal Talent/Recruiting one — matching the published path."""

    def _run(self, monkeypatch, generic_addr):
        import asyncio
        from app.api import hunt as h
        from app.scrapers.resolver import ResolutionCache
        from app.scrapers.enricher import HunterEnricher

        async def fake_mx(self, d): return ["mx.example.com"]
        async def fake_pub(d): return None
        async def fake_web(d, company=""): return None
        async def fake_generic(self, d): return generic_addr

        monkeypatch.setattr(ResolutionCache, "mx", fake_mx)
        monkeypatch.setattr(h, "find_published_role_email", fake_pub)
        monkeypatch.setattr(h, "search_role_email_on_web", fake_web)
        monkeypatch.setattr(HunterEnricher, "search_generic", fake_generic)
        monkeypatch.setattr(h.settings, "hunter_api_key", "fake")
        return asyncio.run(h._resolve_domain_contact(
            {"name": "", "company": "Acme", "_domain": "acme.com", "source": "careers-inbox"},
            ResolutionCache(),
        ))

    def test_hunter_general_inbox_is_company_inbox(self, monkeypatch):
        r = self._run(monkeypatch, "contact@acme.com")
        assert r["designation"] == "Company Inbox (role inbox)"

    def test_hunter_hiring_inbox_is_talent_recruiting(self, monkeypatch):
        r = self._run(monkeypatch, "careers@acme.com")
        assert r["designation"] == "Talent/Recruiting (role inbox)"
