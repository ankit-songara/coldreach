"""Pydantic schemas for email composition endpoints."""

from datetime import datetime
from pydantic import BaseModel, Field


# Length caps deliberately conservative: real cold emails are <5000 chars,
# subject lines are <200. Anything past these is either an accidental paste of
# something huge or a malicious payload — rejecting cheaply at the boundary
# beats storing a 10MB body in the DB or letting a 500k-character query burn
# the whole serverless function timeout.
_SUBJECT_MAX  = 500
_BODY_MAX     = 20_000
_CONTEXT_MAX  = 5_000
_QUERY_MAX    = 500
_RESUME_MAX   = 50_000


class ComposeRequest(BaseModel):
    contact_id:      int
    resume:          str = Field("", max_length=_RESUME_MAX)     # empty → fall back to the user's saved résumé
    company_context: str = Field("", max_length=_CONTEXT_MAX)


class FollowUpRequest(BaseModel):
    contact_id:     int
    original_email: str = Field(..., max_length=_BODY_MAX + _SUBJECT_MAX)  # "SUBJECT: ...\n\nBODY: ..."


class DraftCreate(BaseModel):
    contact_id:  int
    subject:     str = Field(..., max_length=_SUBJECT_MAX)
    body:        str = Field(..., max_length=_BODY_MAX)
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
    query:          str = Field(..., min_length=1, max_length=_QUERY_MAX)
    hunter_api_key: str = Field("", max_length=200)   # override env var for one request
    # Optional target-role family ("engineering", "management", "recruiting", …).
    # Empty = no filtering (return every reachable lead, current behaviour).
    role_filter:    str = Field("", max_length=32)
    # "Hunt deeper" re-run: widens the resolve slice (breadth only, same time
    # budgets). Marginal by design — exclusions already make every re-run dig
    # deeper; this squeezes a little more from an all-duplicates dead end.
    deepen:         bool = False


class HuntResult(BaseModel):
    # Per-source breakdowns deliberately stay server-side: where the leads come
    # from is our pipeline detail, not something the product surfaces to users.
    contacts:      list[dict]
    total:         int             # new contacts saved this hunt
    found:         int = 0         # leads discovered (pre-resolution)
    duplicates:    int = 0         # matches already in the user's list (skipped early or at save)
    role_filtered: int = 0         # reachable leads dropped by the role filter
    # The existing contacts that made this hunt's leads duplicates — lets the
    # UI turn "all duplicates" into a review of the pipeline the user already
    # has. Illustrative (deduped, capped), so len() may differ from duplicates.
    duplicate_contacts: list[dict] = []
