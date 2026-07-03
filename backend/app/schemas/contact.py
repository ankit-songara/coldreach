"""Pydantic schemas for Contact endpoints."""

from datetime import datetime
from pydantic import BaseModel, EmailStr


class ContactCreate(BaseModel):
    name:         str = "Contact"
    email:        str
    designation:  str = "Hiring Manager"
    company:      str = "Unknown"
    source:       str = ""
    context:      str | None = None
    status:       str = "new"
    notes:        str | None = None
    confidence:   int = 0
    email_status: str = "unknown"   # unknown | valid | risky | invalid


class ContactUpdate(BaseModel):
    name:            str | None = None
    designation:     str | None = None
    company:         str | None = None
    status:          str | None = None
    notes:           str | None = None
    last_emailed_at: datetime | None = None
    replied_at:      datetime | None = None
    bounced:         bool | None = None
    followups_sent:  int | None = None
    email_status:    str | None = None


class ContactOut(BaseModel):
    id:              int
    name:            str
    email:           str
    designation:     str
    company:         str
    source:          str
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
