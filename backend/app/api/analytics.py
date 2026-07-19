"""
GET /api/analytics/summary — outreach analytics for the v2 Analytics screen.

Everything is computed ON READ from the user's existing contacts rows
(last_emailed_at / replied_at / status / designation) — no new tracking, no
background jobs (Vercel serverless has none), and per-user volumes are small
enough that one get_all() + Python aggregation is plenty.
"""

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.hunt import _role_families
from app.db.database import get_db
from app.db.crud import ContactRepository
from app.db.models import User
from app.deps import get_current_user
from app.timeutil import utcnow

router = APIRouter(prefix="/analytics", tags=["analytics"])

_WEEKS = 6
# Day-parts over the send hour (naive UTC, same as the stored timestamps).
_PARTS = ("morning", "afternoon", "evening")
# All 7 weekdays (0=Mon..6=Sun) are reported — weekend sends do happen and
# folding them into Fri/Mon would misattribute them; the UI can collapse
# empty weekend rows if it wants a 5-day grid.
_WEEKDAYS = range(7)


def _part(hour: int) -> str:
    if hour < 12:
        return "morning"
    if hour <= 17:
        return "afternoon"
    return "evening"


def _rate(replied: int, sent: int) -> float:
    return round(replied / sent, 3) if sent else 0.0


@router.get("/summary")
def analytics_summary(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    contacts = ContactRepository(db, user.id).get_all()

    # ── Weekly send/reply trend: last 6 ISO weeks (oldest → current) ──────────
    today = utcnow().date()
    this_monday = today - timedelta(days=today.weekday())
    weekly = []
    for i in range(_WEEKS - 1, -1, -1):
        start = this_monday - timedelta(weeks=i)
        end = start + timedelta(days=7)
        sent = sum(1 for c in contacts
                   if c.last_emailed_at and start <= c.last_emailed_at.date() < end)
        replied = sum(1 for c in contacts
                      if c.replied_at and start <= c.replied_at.date() < end)
        weekly.append({"week_start": start.isoformat(), "sent": sent,
                       "replied": replied, "rate": _rate(replied, sent)})

    # ── Send-time histogram: weekday × day-part over last_emailed_at ──────────
    # "replied" here = sends from that cell that EVER got a reply (replied_at
    # set), answering "when do my sends work best".
    cells = {(wd, p): {"sent": 0, "replied": 0} for wd in _WEEKDAYS for p in _PARTS}
    for c in contacts:
        if not c.last_emailed_at:
            continue
        cell = cells[(c.last_emailed_at.weekday(), _part(c.last_emailed_at.hour))]
        cell["sent"] += 1
        if c.replied_at:
            cell["replied"] += 1
    send_time = [{"weekday": wd, "part": p, **cells[(wd, p)]}
                 for wd in _WEEKDAYS for p in _PARTS]

    # ── Reply rate by designation family (hunt.py's classifier, reused) ───────
    # A designation can belong to several families ("Engineering Manager" is
    # both engineering and management) — it counts toward each; unclassifiable
    # designations land in "other".
    fam_counts: dict[str, dict] = {}
    for c in contacts:
        if not c.last_emailed_at:
            continue
        for fam in _role_families(c.designation or "") or {"other"}:
            entry = fam_counts.setdefault(fam, {"sent": 0, "replied": 0})
            entry["sent"] += 1
            if c.replied_at:
                entry["replied"] += 1
    by_role = sorted(
        ({"family": fam, "sent": v["sent"], "replied": v["replied"],
          "rate": _rate(v["replied"], v["sent"])} for fam, v in fam_counts.items()),
        key=lambda r: (-r["rate"], -r["sent"], r["family"]),
    )

    # ── Funnel totals ─────────────────────────────────────────────────────────
    sent_total    = sum(1 for c in contacts if c.last_emailed_at)
    replied_total = sum(1 for c in contacts if c.replied_at)
    totals = {
        "sent":       sent_total,
        "replied":    replied_total,
        "interviews": sum(1 for c in contacts if c.status == "interview"),
        "offers":     sum(1 for c in contacts if c.status == "offer"),
        "reply_rate": _rate(replied_total, sent_total),
    }

    return {"weekly": weekly, "send_time": send_time, "by_role": by_role, "totals": totals}
