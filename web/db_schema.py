from sqlalchemy import (
    MetaData, Table, Column, Text, String,
    Integer, DateTime, JSON, ForeignKey, Boolean, text,
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
