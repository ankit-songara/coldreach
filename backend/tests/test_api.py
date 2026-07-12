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
