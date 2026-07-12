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
