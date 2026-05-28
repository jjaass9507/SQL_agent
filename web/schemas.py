"""
Request and response schemas (Pydantic v2).
Documentation-only: defines types for OpenAPI spec alignment.
Not yet wired into app.py route validation.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ── Request models ─────────────────────────────────────

class CreateSessionRequest(BaseModel):
    title: str = "未命名設計"
    mode: Literal["design", "review"] = "design"
    db_url: Optional[str] = None
    db_schema: str = "public"


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=10_000)


class ImportDbRequest(BaseModel):
    db_url: str = Field(min_length=1)
    db_schema: str = "public"


# ── Column / Table response models ────────────────────

class ColumnSpecResponse(BaseModel):
    name: str
    data_type: str
    length: Optional[int] = None
    nullable: bool
    default: Optional[str] = None
    description: str
    is_primary_key: bool
    is_foreign_key: bool
    references: Optional[str] = None
    is_unique: bool
    is_indexed: bool


class TableSpecResponse(BaseModel):
    table_name: str
    description: str
    columns: list[ColumnSpecResponse]
    constraints: list[str]
    related_tables: list[str]


# ── Session response models ────────────────────────────

class SessionSummary(BaseModel):
    id: str
    title: str
    mode: Literal["design", "review"]
    phase: str
    created_at: str
    table_count: int


class VersionSummary(BaseModel):
    version: int
    created_at: str
    table_count: int


class SessionDetail(BaseModel):
    id: str
    title: str
    mode: Literal["design", "review"]
    phase: str
    created_at: str
    messages: list[dict]
    tables: Optional[list[TableSpecResponse]] = None
    key_points: list[str]
    outputs: dict[str, str]
    generation_status: dict[str, str]
    generation_errors: dict[str, str]
    table_versions: list[VersionSummary]
    context_tables: Optional[list[TableSpecResponse]] = None


# ── API response models ────────────────────────────────

class SendMessageResponse(BaseModel):
    reply: str
    phase: str
    tables_ready: bool
    tables: Optional[list[TableSpecResponse]] = None
    key_points: list[str]


class CreateSessionResponse(BaseModel):
    id: str
    title: str
    mode: str
    phase: str
    created_at: str
    db_imported: Optional[int] = None
    db_error: Optional[str] = None


class ImportDbResponse(BaseModel):
    imported: int
    tables: list[str]


class OutputsResponse(BaseModel):
    outputs: dict[str, str]
    generation_status: dict[str, str]


class ConfirmResponse(BaseModel):
    status: Literal["generating"]


class RestoreVersionResponse(BaseModel):
    status: Literal["restored"]
    version: int


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str


class ErrorResponse(BaseModel):
    error: str
