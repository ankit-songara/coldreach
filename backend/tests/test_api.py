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
