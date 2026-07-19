"""One-off, idempotent data cleanups run at startup."""

from sqlalchemy.orm import Session
from app.db.models import Contact


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
    purged = q.count()
    if purged:
        q.delete(synchronize_session=False)
        db.commit()
    return purged
