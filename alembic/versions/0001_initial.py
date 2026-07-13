"""initial schema

建立 Phase 2 資料層的全部資料表：users、sessions、messages、schema_versions、
outputs、jobs、change_requests、activity_log、app_settings。

Revision ID: 0001
Revises:
Create Date: 2026-07-13 09:10:48.267427

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "activity_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event", sa.String(length=100), nullable=False),
        sa.Column("detail_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "change_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("db_name", sa.String(length=100), nullable=False),
        sa.Column("ddl", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("dry_run_ok", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','executed','failed')",
            name="ck_change_requests_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('user','admin')", name="ck_users_role"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("mode", sa.String(length=10), nullable=False),
        sa.Column("phase", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("context_text", sa.Text(), nullable=False),
        sa.Column("context_tables_json", sa.JSON(), nullable=True),
        sa.Column("db_url_encrypted", sa.Text(), nullable=True),
        sa.CheckConstraint("mode IN ('design','review')", name="ck_sessions_mode"),
        sa.CheckConstraint(
            "phase IN ('collecting','confirming','generating','done','reviewing','review_done')",
            name="ck_sessions_phase",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_sessions_created_at", "sessions", ["created_at"], unique=False)
    op.create_index("idx_sessions_phase", "sessions", ["phase"], unique=False)
    op.create_index("idx_sessions_user_id", "sessions", ["user_id"], unique=False)
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("progress_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('generate','review','extra')", name="ck_jobs_kind"),
        sa.CheckConstraint("status IN ('queued','running','done','failed')", name="ck_jobs_status"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_jobs_session_id", "jobs", ["session_id"], unique=False)
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=5), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('user','ai')", name="ck_messages_role"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_messages_session_id", "messages", ["session_id", "created_at"], unique=False
    )
    op.create_table(
        "outputs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=100), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "filename", name="uq_outputs_session_filename"),
    )
    op.create_index("idx_outputs_session_id", "outputs", ["session_id"], unique=False)
    op.create_table(
        "schema_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("version_num", sa.Integer(), nullable=False),
        sa.Column("tables_json", sa.JSON(), nullable=True),
        sa.Column("key_points_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "version_num", name="uq_schema_version"),
    )
    op.create_index(
        "idx_schema_versions_session_id",
        "schema_versions",
        ["session_id", "version_num"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_schema_versions_session_id", table_name="schema_versions")
    op.drop_table("schema_versions")
    op.drop_index("idx_outputs_session_id", table_name="outputs")
    op.drop_table("outputs")
    op.drop_index("idx_messages_session_id", table_name="messages")
    op.drop_table("messages")
    op.drop_index("idx_jobs_session_id", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("idx_sessions_user_id", table_name="sessions")
    op.drop_index("idx_sessions_phase", table_name="sessions")
    op.drop_index("idx_sessions_created_at", table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("users")
    op.drop_table("change_requests")
    op.drop_table("app_settings")
    op.drop_table("activity_log")
