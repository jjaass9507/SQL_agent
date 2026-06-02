"""ensure_schema must self-heal an existing table missing additive columns.

Reproduces the PG drift bug (column sessions.memory_synced does not exist) using
SQLite: build an old-style sessions table, then ensure_schema adds the column."""
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from web.db_schema import ensure_schema


def _engine():
    return sa.create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


def _cols(engine, table):
    return {c["name"] for c in sa.inspect(engine).get_columns(table)}


def test_ensure_schema_adds_missing_column():
    engine = _engine()
    # Old-style table without memory_synced (pre-0003 schema)
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT)"))
    assert "memory_synced" not in _cols(engine, "sessions")

    ensure_schema(engine)
    assert "memory_synced" in _cols(engine, "sessions")


def test_ensure_schema_idempotent():
    engine = _engine()
    ensure_schema(engine)          # creates all tables fresh (column already present)
    ensure_schema(engine)          # second call must not error or duplicate
    assert "memory_synced" in _cols(engine, "sessions")


def test_ensure_schema_creates_missing_tables():
    engine = _engine()
    ensure_schema(engine)
    names = set(sa.inspect(engine).get_table_names())
    assert {"sessions", "messages", "activity_log"} <= names
