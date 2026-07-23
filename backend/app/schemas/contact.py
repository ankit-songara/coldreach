"""Pydantic schemas for Contact endpoints."""

import re
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, field_validator

# Strict value sets: a typo'd status from a client would otherwise be stored
# verbatim and silently break every status filter and funnel count.
ContactStatus = Literal[
    "new", "emailed", "followed_up", "replied", "interview", "offer", "rejected", "bounced"
]
EmailStatus = Literal["unknown", "valid", "risky", "invalid"]

# Basic well-formed-email check: local@domain.tld, one @, no whitespace.
# Deliberately lenient (accepts role inboxes like "jobs@acme.com") — the
# deliverability verifier does the actual reachability check downstream.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ContactCreate(BaseModel):
    name:         str = Field("Contact",         max_length=255)
    email:        str = Field(...,               max_length=255)
    designation:  str = Field("Hiring Manager",  max_length=255)
    company:      str = Field("Unknown",         max_length=255)
    source:       str = Field("",                max_length=255)
    context:      str | None = Field(None,       max_length=4_000)
    linkedin_url: str | None = Field(None,       max_length=255)
    status:       ContactStatus = "new"
    notes:        str | None = Field(None,       max_length=2_000)
    confidence:   int = 0
    email_status: EmailStatus = "unknown"

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if not _EMAIL_RE.match(v):
            # 422 with a clear message beats silently accepting "not@an@email"
            # into the contacts table where it later fails every send attempt.
            raise ValueError("Enter a valid email address (name@domain.tld).")
        return v


class ContactUpdate(BaseModel):
    name:            str | None = Field(None, max_length=255)
    designation:     str | None = Field(None, max_length=255)
    company:         str | None = Field(None, max_length=255)
    linkedin_url:    str | None = Field(None, max_length=255)
    status:          ContactStatus | None = None
    notes:           str | None = Field(None, max_length=2_000)
    last_emailed_at: datetime | None = None
    replied_at:      datetime | None = None
    bounced:         bool | None = None
    followups_sent:  int | None = None
    email_status:    EmailStatus | None = None


class ContactOut(BaseModel):
    # `source` (which board/site produced the lead) is intentionally NOT
    # exposed — it's pipeline detail, kept in the DB for internal analytics only.
    id:              int
    name:            str
    email:           str
    designation:     str
    company:         str
    status:          str
    notes:           str | None
    last_emailed_at: datetime | None = None
    replied_at:      datetime | None = None
    bounced:         bool = False
    followups_sent:  int = 0
    email_status:    str = "unknown"
    confidence:      int = 0
    linkedin_url:    str | None = None
    created_at:      datetime
    updated_at:      datetime

    model_config = {"from_attributes": True}
