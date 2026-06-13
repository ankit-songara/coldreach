"""Shared pytest fixtures."""

import os, tempfile, pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.db.database import Base, get_db


@pytest.fixture(autouse=True)
def _no_scheduler(monkeypatch):
    """Don't spawn the background follow-up task during tests — it leaks an
    asyncio task across the many lifespan start/stop cycles and produces noisy
    teardown errors. The scheduler has its own coverage elsewhere."""
    import app.main as main_mod

    async def _noop_stop():
        return None

    monkeypatch.setattr(main_mod.scheduler, "start", lambda: None)
    monkeypatch.setattr(main_mod.scheduler, "stop", _noop_stop)
    yield


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
    from app.db.models import Contact, EmailDraft, Resume, User
    db = SessionTest()
    db.query(EmailDraft).delete()
    db.query(Contact).delete()
    db.query(Resume).delete()
    db.query(User).delete()
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
