"""Shared pytest fixtures."""

import os, tempfile, pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.db.database import Base, get_db


@pytest.fixture(autouse=True)
def _reset_login_throttle():
    """The auth throttle is in-memory and keyed by IP; every test hits it from
    the same TestClient address, so clear it between tests."""
    from app.api import auth as auth_mod
    auth_mod._login_attempts.clear()
    yield
    auth_mod._login_attempts.clear()


@pytest.fixture(scope="session")
def test_engine():
    """File-based SQLite so all connections share the same tables."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()   # release the file handle before unlinking (Windows)
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def client(test_engine):
    """TestClient with DB overridden to the test engine."""
    SessionTest = sessionmaker(bind=test_engine)

    def override_get_db():
        db = SessionTest()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()

    # Wipe all rows between tests (keep schema)
    from app.db.models import (
        Contact, EmailDraft, Resume, ResumeFile, User, AppConfig, KnownCompany, EmailPattern,
    )
    db = SessionTest()
    db.query(EmailDraft).delete()
    db.query(AppConfig).delete()
    db.query(Contact).delete()
    db.query(Resume).delete()
    db.query(ResumeFile).delete()
    db.query(KnownCompany).delete()
    db.query(EmailPattern).delete()
    db.query(User).delete()
    db.commit()
    db.close()

    # The directory's in-memory runtime registry is process-global — reset it so
    # companies registered in one test don't leak into the next.
    from app.scrapers import directory
    directory._RUNTIME.clear()


@pytest.fixture
def db_session(test_engine):
    """A raw DB session on the test engine, for unit-testing crud helpers.
    Cleans up its own EmailPattern rows (the table it's used with)."""
    SessionTest = sessionmaker(bind=test_engine)
    db = SessionTest()
    yield db
    from app.db.models import EmailPattern
    db.query(EmailPattern).delete()
    db.commit()
    db.close()


@pytest.fixture
def auth_client(client):
    """A TestClient with a registered user's bearer token pre-set on its headers."""
    r = client.post("/api/auth/register", json={
        "email": "tester@example.com", "password": "password123",
    })
    assert r.status_code == 200, r.text
    client.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return client


@pytest.fixture(autouse=True)
def _clear_grounding_cache():
    """The role-email grounding cache is module-level (per-process) — clear it
    so tests that reuse a domain with different mock responses stay isolated."""
    from app.scrapers import web
    web._ground_cache.clear()
    yield
    web._ground_cache.clear()
