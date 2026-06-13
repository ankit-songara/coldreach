"""Regression tests for the correctness/security fixes."""

import pytest

from app import security
from app.llm.factory import create_llm, detect_provider
from app.config import settings
from app.timeutil import to_naive_utc
from datetime import datetime, timezone


class TestTokenRevocation:
    def test_token_roundtrip_carries_version(self):
        tok = security.create_token(7, token_version=3)
        payload = security.verify_token(tok)
        assert payload["uid"] == 7
        assert payload["ver"] == 3

    def test_logout_revokes_old_tokens(self, auth_client):
        # /auth/me works with the issued token...
        assert auth_client.get("/api/auth/me").status_code == 200
        # ...until logout bumps the user's token_version.
        assert auth_client.post("/api/auth/logout").status_code == 200
        assert auth_client.get("/api/auth/me").status_code == 401


class TestMockSafety:
    def test_auto_never_returns_mock(self, monkeypatch):
        import asyncio
        # Force the no-provider path: not auto-forced, no Ollama, no key.
        monkeypatch.setattr(settings, "llm_provider", "auto")
        monkeypatch.setattr(settings, "llm_api_key", "")
        monkeypatch.setattr(settings, "ollama_base_url", "http://127.0.0.1:9")  # nothing here
        with pytest.raises(RuntimeError):
            asyncio.run(detect_provider())

    def test_mock_output_is_unsendable_placeholder(self):
        llm = create_llm("mock", "mock")
        from langchain_core.messages import HumanMessage
        result = llm.invoke([HumanMessage(content="company: Acme")])
        assert "MOCK DRAFT" in result.content
        assert "Do not send" in result.content


class TestTimeUtil:
    def test_aware_to_naive_utc(self):
        aware = datetime(2026, 6, 14, 9, 0, tzinfo=timezone.utc)
        naive = to_naive_utc(aware)
        assert naive.tzinfo is None
        assert naive.hour == 9

    def test_naive_passthrough(self):
        naive = datetime(2026, 6, 14, 9, 0)
        assert to_naive_utc(naive) == naive


class TestVerifier:
    def test_syntax_invalid(self):
        from app.verifier import verify_email
        assert verify_email("not-an-email") == "invalid"

    def test_disposable_invalid(self):
        from app.verifier import verify_email
        assert verify_email("foo@mailinator.com") == "invalid"
