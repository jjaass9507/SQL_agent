"""ORM models（SQLAlchemy 2.0 declarative）。

依 docs/db_schema.md 的目標 schema 實作，並依 docs/v2_rebuild_plan.md 第五章調整：
table_specs/column_specs 收斂為 schema_versions.tables_json，
context_table_specs 收斂為 sessions.context_tables_json。

刻意避免 PostgreSQL 專屬型態（不用 ARRAY，JSON 一律用 SQLAlchemy 通用 JSON type），
確保 SQLite（開發/測試）與 PostgreSQL（正式）共用同一套 model。
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    """統一的建立時間預設值（UTC）。"""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """所有 ORM model 的共同基底。"""


class User(Base):
    """使用者帳號（Phase 7 認證上線後啟用；AD 登入見 app/services/ad_auth.py）。"""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('user','admin')", name="ck_users_role"),
        CheckConstraint("auth_source IN ('local','ad')", name="ck_users_auth_source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    # AD 使用者（auth_source='ad'）恆為 NULL：AD 帳密不落地，一律由 AD 驗證。
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    # 身分驗證來源：'local'（email+密碼）或 'ad'（AD JIT 供裝，見 app/services/ad_auth.py）。
    auth_source: Mapped[str] = mapped_column(String(20), nullable=False, default="local")
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class SessionRecord(Base):
    """設計/審查工作階段（對應前端一個 session）。"""

    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint("mode IN ('design','review')", name="ck_sessions_mode"),
        CheckConstraint(
            "phase IN ('collecting','confirming','generating','done','reviewing','review_done')",
            name="ck_sessions_phase",
        ),
        Index("idx_sessions_user_id", "user_id"),
        Index("idx_sessions_created_at", "created_at"),
        Index("idx_sessions_phase", "phase"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="未命名設計")
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="design")
    phase: Mapped[str] = mapped_column(String(20), nullable=False, default="collecting")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    context_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 匯入現有 DB 時的既有結構（收斂自 context_table_specs，見模組 docstring）
    context_tables_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # AES-256-GCM 加密後的 DB 連線字串（見 app/repos/crypto.py）
    db_url_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)


class Message(Base):
    """對話訊息（Interviewer / DB Agent）。"""

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user','ai')", name="ck_messages_role"),
        Index("idx_messages_session_id", "session_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(5), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class SchemaVersion(Base):
    """Schema 快照版本（每個 session 最多保留 10 版，由 repo 層強制）。"""

    __tablename__ = "schema_versions"
    __table_args__ = (
        UniqueConstraint("session_id", "version_num", name="uq_schema_version"),
        Index("idx_schema_versions_session_id", "session_id", "version_num"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    version_num: Mapped[int] = mapped_column(Integer, nullable=False)
    # 收斂自 table_specs/column_specs：list[TableSpec] 的 JSON
    tables_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 收斂自 key_points 表：該版本的需求摘要要點
    key_points_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class Output(Base):
    """生成產出的文件內容（規格書 / DDL / ER 圖 / 安全規劃…）。"""

    __tablename__ = "outputs"
    __table_args__ = (
        UniqueConstraint("session_id", "filename", name="uq_outputs_session_filename"),
        Index("idx_outputs_session_id", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class Job(Base):
    """背景工作（生成 / 審查 / on-demand extra），由 asyncio worker 輪詢處理。"""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("kind IN ('generate','review','extra')", name="ck_jobs_kind"),
        CheckConstraint("status IN ('queued','running','done','failed')", name="ck_jobs_status"),
        Index("idx_jobs_session_id", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="queued")
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    progress_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChangeRequest(Base):
    """HITL 結構變更提案（DB Agent 的 propose_ddl 產生，管理員審批後執行）。"""

    __tablename__ = "change_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected','executed','failed')",
            name="ck_change_requests_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    db_name: Mapped[str] = mapped_column(String(100), nullable=False)
    ddl: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")
    dry_run_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ActivityLog(Base):
    """結構化 audit log（見 docs/security_design.md 第七章）。"""

    __tablename__ = "activity_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    event: Mapped[str] = mapped_column(String(100), nullable=False)
    detail_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class AppSetting(Base):
    """平台層級的鍵值設定（例如 LLM CapabilityProfile）。"""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value_json: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSON, nullable=True
    )
