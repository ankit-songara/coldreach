"""Contacts CRUD routes — /api/contacts"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.crud import ContactRepository
from app.db.models import User
from app.deps import get_current_user
from app.schemas.contact import ContactCreate, ContactUpdate, ContactOut

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
