"""
Datetime helpers.

The database stores naive datetimes in UTC (SQLAlchemy `func.now()` and our
`datetime.utcnow()` writes). Clients, however, may submit timezone-aware ISO
strings (e.g. "2026-06-14T09:00:00+05:30"). Comparing an aware value against a
naive `utcnow()` raises `TypeError`, so we normalise every inbound datetime to
naive UTC at the API boundary.
"""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Naive UTC now — matches what the DB columns store."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(dt: datetime) -> datetime:
    """Coerce any datetime to naive UTC. Naive input is assumed to be UTC."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt
