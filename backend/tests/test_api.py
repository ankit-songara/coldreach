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
        ("Talent/Recruiting (role inbox)", 0),   # P0: careers@/jobs@ inbox first
        ("Founder",                        1),   # P1 tier 1
        ("CTO",                            1),
        ("Technical Recruiter",            2),   # P1 tier 2 (named HR/TA person)
        ("Software Engineer",              3),   # P1 tier 3
        ("Office Manager",                 4),
    ])
    def test_p0_careers_inbox_sorts_before_named_people(self, designation, priority):
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

    def test_vercel_env_skips_dead_smtp_loop_and_still_returns_a_guess(self, monkeypatch):
        """
        Vercel blocks outbound port 25, so _smtp_probe always returns None
        there (verified against resolver.py's own VERCEL check) — meaning
        detect_catch_all always reports False and the confirmation loop can
        NEVER succeed on the one platform this app is actually deployed to.
        Without the VERCEL short-circuit, this fallback would be entirely
        inert in production. Confirmed here by NOT mocking _smtp_probe at all
        — if the code path reached it, this test would hang/fail on a real
        network call instead of returning immediately.
        """
        import asyncio
        from app.api import hunt as hunt_mod
        from app.scrapers.resolver import ResolutionCache

        async def fake_mx(self, domain):
            return ["mx.example.com"]

        async def fake_catch_all(self, domain, mx):
            return False   # not catch-all — the SMTP loop would normally run here

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
        assert result is not None
        assert result["email"] == "careers@acme.com"
        assert result["email_status"] == "risky"

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

        monkeypatch.setattr(hunt_mod, "emails_from_company_pages", tracking_page_emails)
        monkeypatch.setattr(hunt_mod, "find_published_role_email", fake_published)
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

        monkeypatch.setattr(ResolutionCache, "mx", fake_mx)
        monkeypatch.setattr(hunt_mod, "find_published_role_email", fake_published)
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

    def test_blind_guess_labeled_unverified_and_demoted(self, monkeypatch):
        """
        When nothing grounds the address (no published page, no Hunter data),
        the fallback guess must be honestly labeled '(unverified guess)' —
        distinct from a grounded '(role inbox)' find — so it sorts below P1
        named people and is excluded from Send.tsx's default bulk-send list.
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

        monkeypatch.setattr(ResolutionCache, "mx", fake_mx)
        monkeypatch.setattr(ResolutionCache, "catch_all", fake_catch_all)
        monkeypatch.setattr(hunt_mod, "find_published_role_email", fake_published)
        monkeypatch.setattr(hunt_mod.settings, "hunter_api_key", "")

        cache = ResolutionCache()
        result = asyncio.run(hunt_mod._resolve_domain_contact(
            {"name": "", "company": "Acme", "_domain": "acme.com", "source": "careers-inbox"},
            cache,
        ))
        assert result is not None
        assert result["designation"] == "Talent/Recruiting (unverified guess)"
        assert hunt_mod._desig_priority(result["designation"]) == 5
        from app.llm.prompts import get_designation_key
        assert get_designation_key(result["designation"]) == "hiring_inbox"


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
        from app.db.models import Contact, User
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

        purged = purge_unverified_role_inbox_guesses(db_session)
        assert purged == 1
        remaining = {c.email for c in db_session.query(Contact).all()}
        assert "careers@gone1.com" not in remaining
        assert {"careers@gone2.com", "hiring@keep.com",
                "careers@keep2.com", "sarah@keep3.com"} <= remaining

        # Idempotent — second run is a no-op.
        assert purge_unverified_role_inbox_guesses(db_session) == 0


class TestHuntSuggestions:
    def test_suggestions_returns_hiring_companies(self, auth_client, monkeypatch):
        import httpx
        from app.api import hunt as hunt_mod

        hunt_mod._suggest_cache.update(at=0.0, companies=[])

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[
                {"legal": "meta"},
                {"company": "Acme Labs", "position": "Senior Backend Engineer"},
                {"company": "Acme Labs", "position": "SDE II"},
                {"company": "Marketing Co", "position": "Growth Marketer"},
                {"company": "Zed", "position": "Fullstack Developer"},
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
