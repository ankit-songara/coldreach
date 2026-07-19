"""One-off, idempotent data cleanups run at startup."""

from sqlalchemy.orm import Session
from app.db.models import Contact, EmailDraft


def purge_unverified_role_inbox_guesses(db: Session) -> int:
    """
    Delete never-emailed contacts created by the pre-grounding hunt: blind
    careers@-style guesses that were labeled "(role inbox)" with "risky"
    status and so were included in bulk send, bouncing in production.

    Post-fix contacts never match: grounded role inboxes are email_status
    "valid", and remaining guesses carry the "(unverified guess)" label.
    """
    q = (
        db.query(Contact)
        .filter(
            Contact.designation.like("%(role inbox)%"),
            Contact.email_status == "risky",
            Contact.status == "new",
            Contact.last_emailed_at.is_(None),
        )
    )
    ids = [c.id for c in q.all()]
    if ids:
        # These tables have no FK cascade (see ContactRepository.delete) — the
        # contacts' drafts must go too or they orphan forever.
        db.query(EmailDraft).filter(EmailDraft.contact_id.in_(ids)).delete(
            synchronize_session=False
        )
        db.query(Contact).filter(Contact.id.in_(ids)).delete(
            synchronize_session=False
        )
        db.commit()
    return len(ids)
