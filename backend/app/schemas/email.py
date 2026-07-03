"""Pydantic schemas for email composition endpoints."""

from datetime import datetime
from pydantic import BaseModel


class ComposeRequest(BaseModel):
    contact_id:      int
    resume:          str = ""     # empty → fall back to the user's saved résumé
    company_context: str = ""


class FollowUpRequest(BaseModel):
    contact_id:     int
    original_email: str          # "SUBJECT: ...\n\nBODY: ..."


class DraftCreate(BaseModel):
    contact_id:  int
    subject:     str
    body:        str
    is_followup: bool = False


class DraftOut(BaseModel):
    id:          int
    contact_id:  int
    subject:     str
    body:        str
    is_followup: bool
    created_at:  datetime

    model_config = {"from_attributes": True}


class HuntRequest(BaseModel):
    query:          str
    hunter_api_key: str = ""    # optional — overrides env var for this request


class HuntResult(BaseModel):
    contacts:   list[dict]
    total:      int                # new contacts saved this hunt
    sources:    dict[str, int]     # {"HackerNews": 3, "GitHub": 2, ...}
    found:      int = 0            # leads discovered across sources (pre-resolution)
    duplicates: int = 0            # resolved contacts already in the user's list
