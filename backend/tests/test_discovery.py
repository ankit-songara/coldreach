"""Tests for the daily company-discovery job (app.jobs.discover_companies).

The persistence path is exercised directly (no network); the HN source is tested
with a mocked fetch so nothing hits the wire.
"""

import asyncio

import pytest
from sqlalchemy.orm import sessionmaker

from app.jobs import discover_companies as disc
from app.db.models import KnownCompany
from app.scrapers import directory


@pytest.fixture
def disc_db(test_engine):
    """Raw session for the discovery crud path; cleans KnownCompany rows and the
    process-global directory runtime registry so tests don't leak into each other."""
    Session = sessionmaker(bind=test_engine)
    db = Session()
    yield db
    db.rollback()
    db.query(KnownCompany).delete()
    db.commit()
    db.close()
    directory._RUNTIME.clear()


def test_persist_inserts_new_discovered_companies(disc_db):
    slug = "zzdiscotest1"
    assert not directory.is_known("greenhouse", slug)
    added = disc.persist_mappings(disc_db, [
        {"ats": "greenhouse", "slug": slug, "company": "Disco Test 1", "domain": "Disco1.com"},
    ])
    assert added == 1
    row = disc_db.query(KnownCompany).filter_by(ats="greenhouse", slug=slug).one()
    assert row.source == "discovered"
    assert row.name == "Disco Test 1"
    assert row.domain == "disco1.com"          # add_known_company lowercases the domain
    assert directory.is_known("greenhouse", slug)   # now in the live directory


def test_persist_is_idempotent(disc_db):
    m = [{"ats": "lever", "slug": "zzdiscotest2", "company": "Disco Test 2", "domain": ""}]
    assert disc.persist_mappings(disc_db, m) == 1
    assert disc.persist_mappings(disc_db, m) == 0   # already known → skipped, no dup
    assert disc_db.query(KnownCompany).filter_by(slug="zzdiscotest2").count() == 1


def test_persist_skips_already_known(disc_db):
    directory.register("Seeded Co", "zzdiscotest3", "ashby", "seeded.com")
    added = disc.persist_mappings(disc_db, [
        {"ats": "ashby", "slug": "zzdiscotest3", "company": "Seeded Co", "domain": ""},
    ])
    assert added == 0
    assert disc_db.query(KnownCompany).filter_by(slug="zzdiscotest3").count() == 0


def test_persist_skips_invalid_rows(disc_db):
    added = disc.persist_mappings(disc_db, [
        {"ats": "notanats", "slug": "x", "company": "Nope"},      # ats not discoverable
        {"ats": "greenhouse", "slug": "", "company": "NoSlug"},   # empty slug
        {"ats": "greenhouse", "company": "MissingSlugKey"},       # no slug key at all
    ])
    assert added == 0
    assert disc_db.query(KnownCompany).count() == 0


def test_source_hackernews_persists(monkeypatch, disc_db):
    """_source_hackernews wires the (mocked) HN fetch to persistence — no network."""
    async def fake_load_thread():
        return []
    monkeypatch.setattr(disc.hackernews, "_load_thread", fake_load_thread)
    monkeypatch.setattr(disc.hackernews, "harvested_mappings",
                        lambda: [{"ats": "workable", "slug": "zzdiscotest4",
                                  "company": "HN Co", "domain": "hnco.com"}])
    result = asyncio.run(disc._source_hackernews(disc_db))
    assert result == {"source": "hackernews", "seen": 1, "added": 1}
    assert disc_db.query(KnownCompany).filter_by(slug="zzdiscotest4").one().source == "discovered"


def test_run_refuses_sqlite(monkeypatch):
    """The job must refuse to run against the throwaway local SQLite default,
    rather than silently writing discoveries to a database nobody reads."""
    monkeypatch.setattr(disc.settings, "database_url", "sqlite:///./data/x.db")
    with pytest.raises(SystemExit):
        disc.run()
