"""
Regression tests for hunt precision + email-generation quality.

Locks in behavior that is easy to silently regress.
"""

import asyncio
import pytest

from app.api.hunt import _company_from_email
from app.llm.factory import create_llm
from app.llm.generator import (
    EmailGenerator, _clean_subject, _first_name, _humanize, _wrap,
)
from app.llm.parsing import parse_subject_body
from app.llm.prompts import TEMPLATES, get_designation_key
from app.scrapers.base import person_name_from_email
from app.scrapers.directory import role_match

# Unicode chars used in test strings — defined as constants to keep source ASCII-clean
EM = "—"   # em dash
EN = "–"   # en dash
LDQ = "“"  # left double curly quote
RDQ = "”"  # right double curly quote


# ── Hunt precision ────────────────────────────────────────────────────────────

class TestRoleMatch:
    def test_tech_token_required_when_present(self):
        assert role_match("react engineer hiring", "Senior React Developer")
        assert not role_match("react engineer hiring", "Senior Software Engineer")

    def test_aliases(self):
        assert role_match("golang hiring", "Backend Engineer (Go)")
        assert role_match("go developer", "Golang Engineer")
        assert role_match("javascript hiring", "Senior JS Developer")
        assert role_match("ml engineer", "Machine Learning Engineer")

    def test_word_boundaries(self):
        assert not role_match("golang hiring", "Governance Analyst")
        assert not role_match("java hiring", "JavaScript Developer")

    def test_postgres_postgresql_symmetric(self):
        assert role_match("postgres hiring", "PostgreSQL Developer")
        assert role_match("postgresql hiring", "Backend Engineer (Postgres)")
        assert not role_match("postgresql hiring", "Business Analyst")

    def test_generic_fallback_without_tech_token(self):
        assert role_match("founding engineer", "Founding Engineer")
        assert role_match("senior hiring", "Senior Product Manager")

    def test_empty_query_matches_everything(self):
        assert role_match("hiring", "Anything At All")


class TestPersonNameFromEmail:
    def test_person_like_locals(self):
        assert person_name_from_email("sarah.chen@acme.com", "Acme") == "Sarah Chen"
        assert person_name_from_email("sarah@acme.com", "Acme") == "Sarah"

    def test_role_mailboxes_yield_empty(self):
        for local in ("jobs", "careers", "hr", "talent", "info", "hello"):
            assert person_name_from_email(f"{local}@acme.com", "Acme") == "", local

    def test_company_mailbox_and_digits_yield_empty(self):
        assert person_name_from_email("acme@acme.com", "Acme") == ""
        assert person_name_from_email("dev123@acme.com", "Acme") == ""


class TestCompanyFromEmail:
    def test_corporate_domain(self):
        assert _company_from_email("jobs@acme-labs.io") == "Acme Labs"

    def test_cctld(self):
        assert _company_from_email("a@acme.co.uk") == "Acme"

    def test_freemail_yields_empty(self):
        assert _company_from_email("x@gmail.com") == ""
        assert _company_from_email("x@outlook.com") == ""


# ── Email generation quality ──────────────────────────────────────────────────

class TestFirstName:
    def test_real_names(self):
        assert _first_name("Sarah Chen") == "Sarah"
        assert _first_name("PRIYA") == "Priya"

    def test_placeholder_names_rejected(self):
        # "john doe" is a canonical test fixture (TEST_IDENTITY_NAMES) and
        # dotted email-style strings aren't display names — never greet with
        # either. (This used to expect "John"; the plausibility check
        # deliberately tightened.)
        assert _first_name("john.doe") == ""
        assert _first_name("John Doe") == ""

    def test_role_words_and_usernames_rejected(self):
        for bad in ("Hr", "Talent", "Jobs", "Careers", "jsmith84", "Contact",
                    "hiring manager", "Info", "x", ""):
            assert _first_name(bad) == "", bad


class TestParseSubjectBody:
    def test_plain_markers(self):
        assert parse_subject_body("SUBJECT: hi\n\nBODY:\nworld") == ("hi", "world")

    def test_missing_body_marker_excludes_subject_line(self):
        subject, body = parse_subject_body("SUBJECT: hi\n\nline one\nline two")
        assert subject == "hi"
        assert body == "line one\nline two"

    def test_no_markers_falls_back(self):
        assert parse_subject_body("just text", "fb") == ("fb", "just text")


class TestHumanize:
    def test_em_dash_becomes_comma(self):
        out = _humanize(f"I built X {EM} it works.")
        assert EM not in out
        assert "," in out

    def test_salary_range_k_suffix_preserved(self):
        # $150k—$200k must keep a hyphen, not become "$150k, $200k"
        out = _humanize(f"They pay $150k{EM}$200k plus equity.")
        assert "$150k-$200k" in out, repr(out)

    def test_curly_quotes_normalised(self):
        out = _humanize(f"{LDQ}yes{RDQ}. Ship it!")
        assert out == '"yes". Ship it.'

    def test_pleasantry_sentence_dropped_at_start(self):
        out = _humanize("I hope this email finds you well. Your pipeline caught my eye.")
        assert "finds you well" not in out
        assert "Your pipeline" in out

    def test_pleasantry_dropped_mid_body(self):
        # Mid-body pleasantry must also be stripped, not just leading ones
        out = _humanize(
            "Your Go work caught my eye. "
            "I hope this email finds you well. "
            "I rebuilt our pipeline."
        )
        assert "finds you well" not in out
        assert "Your Go work" in out

    def test_ai_opener_clause_cut(self):
        assert _humanize("I wanted to reach out because I built X.") == "I built X."
        assert _humanize("I noticed that your team ships weekly.") == "Your team ships weekly."


class TestSignoffRegex:
    def test_best_practices_not_stripped(self):
        from app.llm.generator import _SIGNOFF_RE
        body = "Best practices suggest Go.\n\nWorth a chat?"
        result = _SIGNOFF_RE.sub("", body)
        assert "Best practices" in result, repr(result)

    def test_bare_best_on_own_line_is_stripped(self):
        from app.llm.generator import _SIGNOFF_RE
        body = "Check this out.\n\nBest,\nJohn"
        result = _SIGNOFF_RE.sub("", body)
        assert "Best," not in result, repr(result)


class TestCleanSubject:
    def test_trailing_exclamation_stripped(self):
        assert "!" not in _clean_subject("quick question!")

    def test_mock_marker_survives(self):
        assert _clean_subject("[MOCK DRAFT -- configure an LLM]").startswith("[MOCK DRAFT")


class TestWrap:
    def test_greeting_body_signoff_and_links(self):
        out = _wrap(f"Saw your repo {EM} great stuff!", "Sarah Chen", "Ankit",
                    "github.com/ankit | linkedin.com/in/ankit")
        assert out.startswith("Hi Sarah,")
        assert EM not in out and "!" not in out
        assert out.endswith("Best regards,\nAnkit\ngithub.com/ankit | linkedin.com/in/ankit")

    def test_no_links_no_extra_line(self):
        out = _wrap("Some body text here.", "Sarah Chen", "Ankit")
        assert out.endswith("Best regards,\nAnkit")

    def test_unknown_name_greets_plainly(self):
        out = _wrap("Some body text here.", "Jobs", "Ankit")
        assert out.startswith("Hi,\n")


class TestFollowupThreading:
    def test_subject_forced_to_re_original(self):
        g = EmailGenerator()
        g._llm = create_llm("mock", "mock")
        out = asyncio.run(g.generate_followup(
            name="Sarah Chen", company="Acme",
            original_email="SUBJECT: quick question\n\nBODY:\nHi Sarah,\n\noriginal body\n\nBest regards,\nAnkit",
            sender_name="Ankit",
        ))
        assert out.splitlines()[0] == "SUBJECT: Re: quick question"


class TestDesignationRouting:
    @pytest.mark.parametrize("designation,expected", [
        ("Engineer",            "peer_engineer"),
        ("Backend Developer",   "peer_engineer"),
        ("DevOps",              "peer_engineer"),
        ("CTO",                 "engineering_leader"),
        ("Staff Engineer",      "engineering_leader"),
        ("Engineering Manager", "engineering_leader"),
        ("Founder / Hiring",    "founder"),
        ("CEO",                 "founder"),
        ("Recruiter",           "recruiter"),
        # Shared inboxes (grounded or guessed) get the formal application
        # template, not the person-to-person recruiter one.
        ("Talent/Recruiting (role inbox)",        "hiring_inbox"),
        ("Company Inbox (role inbox)",            "hiring_inbox"),
        ("Talent/Recruiting (unverified guess)",  "hiring_inbox"),
        ("Product Manager",     "product"),
        ("VP Sales",            "business_leader"),
    ])
    def test_routing(self, designation, expected):
        assert get_designation_key(designation) == expected

    def test_all_keys_have_templates(self):
        for key in ("recruiter", "engineering_leader", "peer_engineer", "founder",
                    "product", "business_leader", "followup"):
            assert key in TEMPLATES
