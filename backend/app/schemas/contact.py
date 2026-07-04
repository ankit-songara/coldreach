"""Pydantic schemas for Contact endpoints."""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel

# Strict value sets: a typo'd status from a client would otherwise be stored
# verbatim and silently break every status filter and funnel count.
ContactStatus = Literal[
    "new", "emailed", "followed_up", "replied", "interview", "offer", "rejected", "bounced"
]
EmailStatus = Literal["unknown", "valid", "risky", "invalid"]


class ContactCreate(BaseModel):
    name:         str = "Contact"
    email:        str
    designation:  str = "Hiring Manager"
    company:      str = "Unknown"
    source:       str = ""
    context:      str | None = None
    status:       ContactStatus = "new"
    notes:        str | None = None
    confidence:   int = 0
    email_status: EmailStatus = "unknown"


class ContactUpdate(BaseModel):
    name:            str | None = None
    designation:     str | None = None
    company:         str | None = None
    status:          ContactStatus | None = None
    notes:           str | None = None
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
    created_at:      datetime
    updated_at:      datetime

    model_config = {"from_attributes": True}
