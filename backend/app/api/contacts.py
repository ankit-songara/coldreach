"""Contacts CRUD routes — /api/contacts"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.crud import ContactRepository
from app.db.models import User
from app.deps import get_current_user
from app.schemas.contact import ContactCreate, ContactUpdate, ContactOut
from app.scrapers.web import search_person_linkedin
from app.scrapers.base import plausible_person_name

router = APIRouter(prefix="/contacts", tags=["contacts"])


def _repo(db: Session, user: User) -> ContactRepository:
    return ContactRepository(db, user.id)


@router.get("", response_model=list[ContactOut])
def list_contacts(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _repo(db, user).get_all()


@router.get("/{contact_id}", response_model=ContactOut)
def get_contact(contact_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    contact = _repo(db, user).get_by_id(contact_id)
    if not contact:
        raise HTTPException(404, "Contact not found")
    return contact


@router.post("", response_model=ContactOut, status_code=201)
def create_contact(data: ContactCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _repo(db, user).create(data)


@router.post("/{contact_id}/linkedin")
async def find_contact_linkedin(
    contact_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """On-demand, keyless LinkedIn lookup for one contact — kept OFF the hunt's
    critical path (the hunt only attaches links already in a lead's provenance).
    Runs the search only when the drawer is opened, so it never affects hunt
    timing. A public search-engine lookup; never contacts LinkedIn itself.
    Memoised, and the result is persisted so it's found instantly next time."""
    repo = _repo(db, user)
    contact = repo.get_by_id(contact_id)
    if not contact:
        raise HTTPException(404, "Contact not found")
    if contact.linkedin_url:
        return {"linkedin_url": contact.linkedin_url}

    name = (contact.name or "").strip()
    parts = name.split()
    # Only named individuals — a role inbox ("Careers", "Contact") has no profile.
    if len(parts) < 2 or not plausible_person_name(name, contact.company or ""):
        return {"linkedin_url": None}

    url = await search_person_linkedin(parts[0], parts[-1], contact.company or "")
    if url:
        repo.update(contact_id, ContactUpdate(linkedin_url=url))
    return {"linkedin_url": url}


@router.patch("/{contact_id}", response_model=ContactOut)
def update_contact(contact_id: int, data: ContactUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    contact = _repo(db, user).update(contact_id, data)
    if not contact:
        raise HTTPException(404, "Contact not found")
    return contact


@router.delete("/{contact_id}", status_code=204)
def delete_contact(contact_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not _repo(db, user).delete(contact_id):
        raise HTTPException(404, "Contact not found")


@router.delete("", status_code=200)
def delete_all_contacts(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    count = _repo(db, user).delete_all()
    return {"deleted": count}
