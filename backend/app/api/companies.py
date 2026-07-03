"""
Company directory management — add target companies without touching code.

  GET    /api/companies        list runtime-added companies (+ seed/total counts)
  POST   /api/companies        add a company → ATS board mapping
  DELETE /api/companies/{id}   remove a runtime-added company

The curated seed (companies.csv) is read-only here; this endpoint manages the
runtime layer that extends it. Mappings are global — a verified company→board
mapping helps every hunt.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.database import get_db
from app.db.crud import list_known_companies, add_known_company, delete_known_company
from app.db.models import User
from app.deps import get_current_user
from app.scrapers import directory

log = logging.getLogger(__name__)
router = APIRouter(prefix="/companies", tags=["companies"])

VALID_ATS = {"greenhouse", "lever", "ashby", "smartrecruiters", "recruitee"}


class CompanyIn(BaseModel):
    name:   str
    slug:   str            # the board slug (e.g. "stripe", or "BoschGroup" for SmartRecruiters)
    ats:    str            # greenhouse | lever | ashby | smartrecruiters | recruitee
    domain: str = ""       # real email domain; helps the resolver


class CompanyOut(BaseModel):
    id:     int
    name:   str
    slug:   str
    ats:    str
    domain: str
    source: str            # user | discovered

    model_config = {"from_attributes": True}


class CompanyListOut(BaseModel):
    companies:   list[CompanyOut]   # runtime-added (user + discovered)
    seed_count:  int                # curated companies shipped in companies.csv
    total:       int                # everything the directory can hunt in role mode


@router.get("", response_model=CompanyListOut)
def list_companies(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = list_known_companies(db)
    return CompanyListOut(
        companies=[CompanyOut.model_validate(r) for r in rows],
        seed_count=len(directory._SEED),
        total=len(directory.all_companies()),
    )


@router.post("", response_model=CompanyOut, status_code=201)
def add_company(req: CompanyIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ats = req.ats.strip().lower()
    if ats not in VALID_ATS:
        raise HTTPException(400, f"ats must be one of: {', '.join(sorted(VALID_ATS))}")
    if not req.name.strip() or not req.slug.strip():
        raise HTTPException(400, "name and slug are required")
    kc = add_known_company(db, name=req.name, slug=req.slug, ats=ats, domain=req.domain, source="user")
    if not kc:
        raise HTTPException(400, "Could not add company")
    return CompanyOut.model_validate(kc)


@router.delete("/{company_id}", status_code=204)
def remove_company(company_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not delete_known_company(db, company_id):
        raise HTTPException(404, "Company not found")
