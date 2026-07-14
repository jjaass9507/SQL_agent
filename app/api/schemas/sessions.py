"""sessions / messages / versions API 的 Pydantic request/response models。

驗證規則依 docs/security_design.md 第四章：title ≤ 200 字元、訊息內容 1–10,000 字元、
review 模式必帶 db_url。db_url 只進不出——所有 response model 均不含此欄位。
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.rules.spec_models import TableSpec


class CreateSessionRequest(BaseModel):
    """POST /sessions 請求。"""

    title: str = Field(default="未命名設計", max_length=200)
    mode: Literal["design", "review"] = "design"
    db_url: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _require_db_url_for_review(self) -> "CreateSessionRequest":
        if self.mode == "review" and not self.db_url:
            raise ValueError("review 模式必須提供 db_url")
        return self


class SendMessageRequest(BaseModel):
    """POST /sessions/{id}/messages 請求。"""

    content: str = Field(min_length=1, max_length=10000)


class ImportDbRequest(BaseModel):
    """POST /sessions/{id}/import-db 請求。"""

    db_url: str = Field(min_length=1)


class JobSummary(BaseModel):
    """session 詳情內嵌的工作進度摘要。"""

    id: UUID
    kind: str
    status: str
    progress_json: dict | None = None
    error: str | None = None
    created_at: datetime


class SessionSummary(BaseModel):
    """GET /sessions 列表項目。"""

    id: UUID
    title: str
    mode: str
    phase: str
    created_at: datetime


class SessionDetail(BaseModel):
    """GET /sessions/{id} 詳情：含 phase、最新 tables/key_points、job 進度摘要。"""

    id: UUID
    title: str
    mode: str
    phase: str
    created_at: datetime
    context_tables: list[TableSpec] | None = None
    latest_version: int | None = None
    latest_tables: list[TableSpec] | None = None
    latest_key_points: list[str] | None = None
    jobs: list[JobSummary] = Field(default_factory=list)


class TurnResponse(BaseModel):
    """一輪對話的回應（JSON 模式；SSE 模式以同樣欄位包進 turn_done event）。"""

    reply: str
    tables_ready: bool
    tables: list[TableSpec] | None = None
    summary: list[str] | None = None


class ConfirmResponse(BaseModel):
    """POST /sessions/{id}/confirm 回應。"""

    session_id: UUID
    phase: str
    job_id: UUID


class VersionOut(BaseModel):
    """schema 版本快照。"""

    version_num: int
    tables: list[TableSpec] | None = None
    key_points: list[str] | None = None
    created_at: datetime


class ImportDbResponse(BaseModel):
    """POST /sessions/{id}/import-db 回應（db_url 不回傳）。"""

    table_count: int
    context_tables: list[TableSpec]
