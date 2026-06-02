"""memory_synced must persist in the PostgreSQL storage path.

Uses an in-memory SQLite engine as a stand-in for PostgreSQL — the session_store
PG functions are plain SQLAlchemy Core, so the same code path is exercised."""
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

import pytest


@pytest.fixture
def pg_like(monkeypatch):
    from web.db_schema import metadata
    engine = sa.create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    metadata.create_all(engine)
    monkeypatch.setattr("web.db_engine.get_engine", lambda: engine)
    monkeypatch.setattr("web.db_engine.is_pg_mode", lambda: True)
    return engine


def test_memory_synced_defaults_false(pg_like):
    from web.session_store import create_session, get_session
    s = create_session("設計", mode="design")
    assert s["memory_synced"] is False
    assert get_session(s["id"])["memory_synced"] == False  # noqa: E712 (sqlite may return 0/1)


def test_memory_synced_persists_through_update(pg_like):
    from web.session_store import create_session, update_session, get_session
    sid = create_session("設計", mode="design")["id"]
    update_session(sid, {"memory_synced": True})
    assert get_session(sid)["memory_synced"] == True  # noqa: E712
