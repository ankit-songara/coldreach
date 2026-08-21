"""
Daily background discovery of hot, actively-hiring companies.

Runs OUTSIDE the request path (GitHub Actions cron — see
.github/workflows/daily-discovery.yml) so it can do the uncapped, minutes-long
harvesting the 60s serverless wall forbids inside a hunt. It grows the SHARED
company->ATS directory (KnownCompany, source="discovered") so every user's next
hunt draws from a fresher, larger pool of companies hiring right now.

Why this exists as a cron and not part of a hunt:
  - On serverless, the HN "Who is Hiring" board-links only trickle into the
    directory as a side effect of a hunt that happens to query HN, and even
    then they're capped at _MAX_LEARNED_PER_HUNT (25) so the SELECT+INSERT round
    trips fit the request's time budget. That leaves most of a ~300-600-post
    monthly thread unharvested. Here it runs uncapped, once a day.

v1 source: the HN "Who is Hiring" thread's apply-links — VERIFIED company->board
mappings (hackernews.harvested_mappings). The `_SOURCES` list is the extension
point for adding more discovery sources later (e.g. search-scraper company names
resolved to a live ATS board) without touching the runner.

Run:   python -m app.jobs.discover_companies
Env:   DATABASE_URL must point at the SAME database the app serves. The job
       refuses to run against the default local SQLite (that would write
       discoveries to a throwaway file), so a misconfigured cron fails loudly
       instead of silently doing nothing.
"""

import asyncio
import logging
import sys

from app.config import settings
from app.db.database import SessionLocal
from app.db.crud import add_known_company, load_known_companies_into_directory
from app.scrapers import directory, hackernews

log = logging.getLogger("discover")

# ATS platforms whose (ats, slug) mappings are worth persisting — mirrors
# hunt._DISCOVERABLE_ATS. harvested_mappings() already only emits real ATS
# platforms, so this is belt-and-suspenders against a future source.
_DISCOVERABLE_ATS = {"greenhouse", "lever", "ashby", "smartrecruiters",
                     "recruitee", "workable", "breezy", "teamtailor"}

# Retry the network harvest a few times — the source is best-effort and a
# transient blip should not waste a whole day's run.
_FETCH_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 3.0


def persist_mappings(db, mappings: list[dict], *, source: str = "discovered") -> int:
    """Insert genuinely-new company->ATS mappings; return how many were added.

    Idempotent: already-known (ats, slug) pairs are skipped in-memory
    (directory.is_known) and add_known_company dedupes at the DB level too, so
    re-running the job never creates duplicates. Pure + network-free, so the
    unit tests exercise the whole persistence path without touching HN.
    """
    added = 0
    for m in mappings:
        ats = (m.get("ats") or "").strip().lower()
        slug = (m.get("slug") or "").strip()
        if not slug or ats not in _DISCOVERABLE_ATS:
            continue
        if directory.is_known(ats, slug):
            continue
        try:
            if add_known_company(db, name=m.get("company") or slug, slug=slug,
                                  ats=ats, domain=m.get("domain") or "", source=source):
                added += 1
                log.info("learned %s (%s/%s)", m.get("company") or slug, ats, slug)
        except Exception as e:            # one bad row must not sink the batch
            db.rollback()
            log.warning("skip %s/%s: %s", ats, slug, e)
    return added


async def _fetch_hn_mappings() -> list[dict]:
    """Fetch the current HN 'Who is Hiring' thread and return its harvested
    (ats, slug, company, domain) mappings. Retries on an empty result — the
    fetch swallows its own network errors and returns nothing, so 'empty' is
    the only failure signal we get."""
    for attempt in range(1, _FETCH_ATTEMPTS + 1):
        await hackernews._load_thread()
        maps = hackernews.harvested_mappings()
        if maps:
            return maps
        if attempt < _FETCH_ATTEMPTS:
            hackernews._cache.update(at=0.0)   # bust the TTL so the next call refetches
            await asyncio.sleep(_RETRY_BACKOFF_SECONDS * attempt)
    return []


async def _source_hackernews(db) -> dict:
    """Discovery source: HN 'Who is Hiring' board-link harvest."""
    maps = await _fetch_hn_mappings()
    added = persist_mappings(db, maps)
    return {"source": "hackernews", "seen": len(maps), "added": added}


# Ordered list of discovery sources. Add more here (e.g. search-scraper company
# names resolved to a live ATS board) — the runner loops them, isolating
# failures so one bad source never sinks the run.
_SOURCES = (_source_hackernews,)


async def _run_async() -> dict:
    _db = SessionLocal()
    try:
        load_known_companies_into_directory(_db)   # so is_known covers DB rows
    finally:
        _db.close()
    per_source: list[dict] = []
    for src in _SOURCES:
        db = SessionLocal()
        try:
            per_source.append(await src(db))
        except Exception as e:
            log.exception("source %s failed", getattr(src, "__name__", src))
            per_source.append({"source": getattr(src, "__name__", "?"), "error": str(e)})
        finally:
            db.close()
    return {"sources": per_source, "added": sum(s.get("added", 0) for s in per_source)}


def run() -> dict:
    """Entrypoint. Guards the DB target, runs every source, returns a summary."""
    if settings.database_url.startswith("sqlite"):
        raise SystemExit(
            "DATABASE_URL is not set (or is the local SQLite default). Set it to "
            "the SAME Postgres URL the app serves, or this job would write "
            "discoveries to a throwaway database. Refusing to run."
        )
    return asyncio.run(_run_async())


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    summary = run()
    total = summary["added"]
    for s in summary["sources"]:
        if "error" in s:
            log.error("%s: FAILED (%s)", s["source"], s["error"])
        else:
            log.info("%s: %d new / %d seen", s["source"], s["added"], s["seen"])
    log.info("done — %d new companies added to the directory", total)
    # Success even at 0 new (a quiet day is normal); only a hard failure (DB
    # unreachable, every source errored) should red-flag the Actions run.
    if summary["sources"] and all("error" in s for s in summary["sources"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
