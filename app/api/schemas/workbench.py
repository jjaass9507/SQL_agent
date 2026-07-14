"""SQL 工作台 API 的請求/回應 Pydantic schema。"""

import uuid

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """查詢／EXPLAIN 共用的請求本體。"""

    sql: str = Field(min_length=1, max_length=10_000)


class QueryResult(BaseModel):
    """查詢／EXPLAIN 共用的回應本體。"""

    columns: list[str]
    rows: list[list]
    truncated: bool


class SchemaColumn(BaseModel):
    name: str
    type: str
    nullable: bool
    is_pk: bool
    is_fk: bool
    fk_table: str | None = None


class SchemaTable(BaseModel):
    name: str
    columns: list[SchemaColumn]


class SchemaTreeResponse(BaseModel):
    """`source` 為 "db"（實際連線內省）或 "design"（設計中的最新版本快照）。"""

    source: str
    tables: list[SchemaTable]


class NL2SQLRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2_000)


class NL2SQLResponse(BaseModel):
    sql: str
    explanation: str


class ValidateDDLResponse(BaseModel):
    ok: bool
    error: str | None = None


class DDLImportRequest(BaseModel):
    title: str | None = None
    ddl: str = Field(min_length=1, max_length=100_000)


class DDLImportResponse(BaseModel):
    id: uuid.UUID
    table_count: int
