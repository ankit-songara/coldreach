"""POST /api/hunt — runs all scrapers in parallel, saves results to DB."""

import asyncio
import logging
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.crud import ContactRepository
from app.db.models import User
from app.deps import get_current_user
from app.schemas.contact import ContactCreate
from app.schemas.email import HuntRequest, HuntResult
from app.scrapers.base import is_valid_email
from app.scrapers.hn import HackerNewsScraper
from app.scrapers.github import GitHubScraper
from app.scrapers.web import WebScraper
from app.scrapers.enricher import HunterEnricher
from app.config import settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/hunt", tags=["hunt"])


def _desig_priority(designation: str) -> int:
    """Return sort key: 1 = Founder/CxO, 2 = HR/TA, 3 = Engineer, 4 = other."""
    d = designation.lower()
    if any(k in d for k in ("founder", "co-founder", "ceo", "cto", "chief", "founding")):
        return 1
    if any(k in d for k in ("hr", "human resource", "talent", "recruiter", "recruiting", "people ops", "people partner")):
        return 2
    if any(k in d for k in ("engineer", "developer", "swe", "software", "backend", "frontend", "fullstack", "devops", "data")):
        return 3
    return 4

# ── Registered scrapers — add new sources here ────────────────────────────────
def _build_scrapers(hunter_key: str) -> list:
    scrapers = [
        HackerNewsScraper(),
        GitHubScraper(),
        WebScraper(),
    ]
    key = hunter_key or settings.hunter_api_key
    if key:
        scrapers.append(HunterEnricher(key))
    return scrapers


@router.post("", response_model=HuntResult)
async def hunt(req: HuntRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Multi-source email hunt.
    Scrapers run in parallel, results are deduplicated and persisted.
    """
    log.info(f"Hunt: {req.query!r}")
    scrapers = _build_scrapers(req.hunter_api_key)

    # ── Run scrapers with staggered starts to avoid hammering external APIs ──
    async def staggered(scraper, delay: float):
        await asyncio.sleep(delay)
        return await scraper.safe_search(req.query)

    tasks = [staggered(s, i * 0.5) for i, s in enumerate(scrapers)]
    results_per_scraper = await asyncio.gather(*tasks)

    # ── Flatten, deduplicate by email ─────────────────────────────────────────
    seen: set[str] = set()
    raw: list[dict] = []
    source_counts: dict[str, int] = {}

    for scraper, results in zip(scrapers, results_per_scraper):
        count = 0
        for r in results:
            email = r.get("email", "").lower().strip()
            if email and email not in seen and is_valid_email(email):
                seen.add(email)
                raw.append(r)
                count += 1
        source_counts[scraper.name] = count

    # ── Sort by designation priority: Founders/CxO → HR/TA → Engineers → rest ──
    raw.sort(key=lambda r: _desig_priority(r.get("designation") or ""))

    # ── Persist to DB ─────────────────────────────────────────────────────────
    repo = ContactRepository(db, user.id)
    contacts_to_save = [
        ContactCreate(
            name        = r.get("name") or "Contact",
            email       = r["email"],
            designation = r.get("designation") or "Hiring Manager",
            company     = r.get("company") or "Unknown",
            source      = r.get("source") or "",
            context     = r.get("context") or None,
        )
        for r in raw
    ]
    saved = repo.bulk_create(contacts_to_save)
    log.info(f"Hunt complete: {len(saved)} new contacts saved")

    return HuntResult(
        contacts=[{
            "id":          c.id,
            "name":        c.name,
            "email":       c.email,
            "designation": c.designation,
            "company":     c.company,
            "source":      c.source,
            "status":      c.status,
        } for c in saved],
        total=len(saved),
        sources=source_counts,
    )
