"""
Demo / sample-data seeding — lets a brand-new user (or a screenshot/demo) see a
fully populated dashboard instead of empty states.

  POST   /api/demo/seed   insert a realistic sample pipeline for the current user
  DELETE /api/demo        remove everything this seeded (idempotent)

Safety: every seeded address uses a reserved, non-routable `.example` domain
(RFC 2606/6761), so even if someone hits "Send all" on demo data, nothing can
reach a real person. Seeded rows are tagged with a sentinel in `notes` (hidden
from the UI) so clearing only removes demo data, never the user's real contacts.
"""

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import User, Contact, EmailDraft, Resume
from app.deps import get_current_user
from app.timeutil import utcnow

log = logging.getLogger(__name__)
router = APIRouter(prefix="/demo", tags=["demo"])

SENTINEL = "__seed_demo__"          # hidden marker stored in Contact.notes
RESUME_FILENAME = "Sample résumé (demo)"

# name, email, designation, company, source, status, email_status, confidence,
# emailed_days_ago (None = never sent), reply_gap_days (None = no reply), has_draft
_SEED = [
    ("Priya Nair",     "priya.nair@vercel.example",      "Engineering Manager", "Vercel",      "GitHub/vercel",       "offer",     "valid",   91, 16, 3,  True),
    ("Jordan Lee",     "jordan.lee@linear.example",      "Founder",             "Linear",      "GitHub/linear",       "interview", "valid",   88, 11, 2,  True),
    ("Raj Patel",      "raj.patel@planetscale.example",  "Eng Lead",            "PlanetScale", "GitHub/planetscale",  "interview", "valid",   86, 12, 3,  True),
    ("Marcus Chen",    "marcus@resend.example",          "CTO",                 "Resend",      "GitHub/resend",       "replied",   "valid",   84, 5,  2,  True),
    ("Tom Becker",     "tom.becker@supabase.example",    "Backend Engineer",    "Supabase",    "GitHub/supabase",     "replied",   "valid",   79, 6,  3,  True),
    ("Yuki Tanaka",    "yuki@warp.example",              "Founder",             "Warp",        "HackerNews",          "replied",   "valid",   77, 8,  4,  True),
    ("Grace Lin",      "grace.lin@notion.example",       "Recruiter",           "Notion",      "Ashby/notion",        "replied",   "valid",   72, 7,  2,  True),
    ("Sam Reed",       "sam.reed@zed.example",           "Engineer",            "Zed",         "GitHub/zed",          "emailed",   "valid",   83, 4,  None, True),
    ("Aisha Khan",     "aisha@cursor.example",           "Founding Engineer",   "Cursor",      "HackerNews",          "emailed",   "valid",   60, 2,  None, True),
    ("Sara Okafor",    "sara.okafor@stripe.example",     "Technical Recruiter", "Stripe",      "Greenhouse/stripe",   "emailed",   "risky",   55, 4,  None, True),
    ("Hannah Schmidt", "hannah@remote.example",          "Recruiter",           "Remote",      "RemoteOK",            "emailed",   "risky",   45, 3,  None, True),
    ("Emma Wilson",    "emma.wilson@deel.example",       "People Ops",          "Deel",        "Remotive",            "emailed",   "unknown",  0, 5,  None, True),
    ("Noah Davis",     "noah@brex.example",              "Recruiter",           "Brex",        "Arbeitnow",           "emailed",   "risky",   40, 6,  None, True),
    ("Lena Rossi",     "lena.rossi@ramp.example",        "Talent Partner",      "Ramp",        "Lever/ramp",          "rejected",  "valid",   70, 20, None, True),
    ("Kofi Mensah",    "kofi@flyio.example",             "Senior Engineer",     "Fly.io",      "GitHub/flyio",        "new",       "valid",   82, None, None, True),
    ("Olivia Brown",   "olivia.brown@neon.example",      "Engineer",            "Neon",        "GitHub/neon",         "new",       "valid",   80, None, None, False),
    ("Diego Alvarez",  "diego@render.example",           "Head of Engineering", "Render",      "HackerNews",          "new",       "unknown",  0, None, None, False),
]

_SAMPLE_RESUME = """\
Sample Candidate
Backend / Full-stack Engineer · Remote

EXPERIENCE
Software Engineer, Acme (2022–present)
- Cut p95 API latency 40% by moving hot paths off the ORM to raw SQL + caching.
- Built the billing service (Stripe) handling ~$2M/yr; owned it end to end.
- Mentored 2 junior engineers; ran the on-call rotation for the platform team.

Engineer, Startup Co (2020–2022)
- Shipped the first version of the mobile API (Go) used by 50k+ users.

SKILLS
Go, Python, TypeScript, Postgres, Redis, Docker, AWS

This is sample text loaded with ColdReach demo data — replace it with your own résumé in Setup.
"""


def _draft_for(name: str, company: str) -> tuple[str, str]:
    first = name.split()[0]
    subject = f"quick question, {company.split()[0]}"
    body = (
        f"Hi {first},\n\n"
        f"Saw what {company} is building and it lines up closely with what I've been doing — "
        f"I shipped a billing service on Stripe handling ~$2M/yr and cut our p95 latency 40%.\n\n"
        f"I'd love to help with what you're scaling next. Worth a quick chat?\n\n"
        f"Best regards,\nSample Candidate"
    )
    return subject, body


def _has_demo(db: Session, user_id: int) -> bool:
    return db.query(Contact).filter(
        Contact.user_id == user_id, Contact.notes == SENTINEL
    ).first() is not None


@router.post("/seed")
def seed_demo(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Insert a sample pipeline (contacts + drafts + résumé). No-op if already seeded."""
    if _has_demo(db, user.id):
        return {"seeded": False, "message": "Demo data already present"}

    now = utcnow()
    contacts_made = drafts_made = 0

    for (name, email, desig, company, source, status, estatus, conf,
         emailed_days, reply_gap, has_draft) in _SEED:
        last_emailed = now - timedelta(days=emailed_days) if emailed_days is not None else None
        replied_at = (last_emailed + timedelta(days=reply_gap)) if (last_emailed and reply_gap is not None) else None

        contact = Contact(
            user_id=user.id, name=name, email=email, designation=desig, company=company,
            source=source, status=status, email_status=estatus, confidence=conf,
            notes=SENTINEL, last_emailed_at=last_emailed, replied_at=replied_at,
            context=f"Sample lead at {company} — demo data.",
        )
        db.add(contact)
        db.commit()
        db.refresh(contact)
        contacts_made += 1

        if has_draft:
            subject, body = _draft_for(name, company)
            db.add(EmailDraft(user_id=user.id, contact_id=contact.id,
                              subject=subject, body=body, is_followup=False))
            db.commit()
            drafts_made += 1

    # Seed a résumé only if the user doesn't already have one (don't clobber real data).
    has_resume = db.query(Resume).filter(Resume.user_id == user.id).first() is not None
    if not has_resume:
        db.add(Resume(user_id=user.id, text=_SAMPLE_RESUME, filename=RESUME_FILENAME))
        db.commit()

    log.info(f"[user {user.id}] seeded demo: {contacts_made} contacts, {drafts_made} drafts")
    return {"seeded": True, "contacts": contacts_made, "drafts": drafts_made}


@router.delete("")
def clear_demo(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Remove everything the seeder created for this user. Idempotent."""
    demo_contacts = db.query(Contact).filter(
        Contact.user_id == user.id, Contact.notes == SENTINEL
    ).all()
    ids = [c.id for c in demo_contacts]

    if ids:
        db.query(EmailDraft).filter(
            EmailDraft.user_id == user.id, EmailDraft.contact_id.in_(ids)
        ).delete(synchronize_session=False)
        for c in demo_contacts:
            db.delete(c)

    # Remove the seeded résumé (only the one we created, matched by filename).
    db.query(Resume).filter(
        Resume.user_id == user.id, Resume.filename == RESUME_FILENAME
    ).delete(synchronize_session=False)
    db.commit()

    log.info(f"[user {user.id}] cleared demo: {len(ids)} contacts")
    return {"cleared": len(ids)}
