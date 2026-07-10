from sqlalchemy import (
    MetaData, Table, Column, Text, String,
    Integer, DateTime, JSON, ForeignKey, Boolean, text, inspect,
)

metadata = MetaData()

sessions_table = Table("sessions", metadata,
    Column("id", String(36), primary_key=True),
    Column("title", Text, nullable=False),
    Column("mode", String(20), nullable=False, server_default="design"),
    Column("phase", String(30), nullable=False),
    Column("key_points", JSON, nullable=False, server_default="[]"),
    Column("tables", JSON, nullable=True),
    Column("table_versions", JSON, nullable=False, server_default="[]"),
    Column("outputs", JSON, nullable=False, server_default="{}"),
    Column("generation_status", JSON, nullable=False, server_default="{}"),
    Column("generation_errors", JSON, nullable=False, server_default="{}"),
    Column("context_tables", JSON, nullable=False, server_default="[]"),
    Column("context_text", Text, nullable=False, server_default=""),
    Column("memory_synced", Boolean, nullable=False, server_default=text("false")),
    Column("last_db_import", JSON, nullable=True),
    Column("db_url", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

messages_table = Table("messages", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", String(36),
           ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
    Column("role", String(10), nullable=False),
    Column("content", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

# Platform usage trail. No FK to sessions: records are kept for audit even
# after a session is deleted (session_id may also be null for global events).
activity_log_table = Table("activity_log", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", String(36), nullable=True),
    Column("event", String(50), nullable=False),
    Column("detail", JSON, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

# Human-in-the-loop DDL change requests (Phase 4). Agent-proposed or manually
# submitted DDL sits here as "pending" until an admin approves/rejects it.
change_requests_table = Table("change_requests", metadata,
    Column("id", String(36), primary_key=True),
    Column("db_name", String(255), nullable=True),
    Column("ddl", Text, nullable=False),
    Column("reason", Text, nullable=False, server_default=""),
    Column("status", String(20), nullable=False, server_default="pending"),
    Column("dry_run_ok", Boolean, nullable=False, server_default=text("false")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("decided_at", DateTime(timezone=True), nullable=True),
    Column("error", Text, nullable=True),
)


def platform_table_names() -> set[str]:
    """Tables the platform owns on its storage DB — hidden from the workbench
    when the session's target DB is the same database."""
    return set(metadata.tables.keys()) | {"alembic_version"}


# Columns added to existing tables after the initial schema. create_all() only
# creates missing *tables*, never adds *columns* to an existing one — and the
# Settings-page flow uses create_all (not Alembic) — so additive columns must be
# applied here too. Keyed by table → {column: column DDL}. Append future columns.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "sessions": {
        "memory_synced": "BOOLEAN NOT NULL DEFAULT false",
    },
}


def ensure_schema(engine, platform_schema: str = "public") -> None:
    """Create missing tables and add any missing additive columns (idempotent).

    Uses inspection + plain ``ALTER TABLE ADD COLUMN`` so it works on both
    PostgreSQL (production) and SQLite (tests), avoiding PG-only syntax.

    When platform_schema is not "public", creates the schema first so that
    the engine's search_path (set in db_engine.py) lands the tables there.
    Existing deployments using the default "public" schema are unaffected."""
    if platform_schema and platform_schema != "public":
        with engine.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {platform_schema}"))
    metadata.create_all(engine)
    insp = inspect(engine)
    table_names = set(insp.get_table_names())
    for table, cols in _ADDED_COLUMNS.items():
        if table not in table_names:
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        for name, ddl in cols.items():
            if name not in existing:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
