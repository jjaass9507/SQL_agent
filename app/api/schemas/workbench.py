"""SQL 工作台 API 的請求/回應 Pydantic schema。"""

import uuid

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """查詢／EXPLAIN 共用的請求本體。"""

    sql: str = Field(min_length=1, max_length=10_000)


class BusinessDbQueryRequest(BaseModel):
    """DB Agent 頁工作台的請求：對象是設定頁登錄的業務資料庫，不是 session。"""

    sql: str = Field(min_length=1, max_length=10_000)
    db_name: str | None = None


class BusinessDbNL2SQLRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2_000)
    db_name: str | None = None


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


class ValidateDDLTextRequest(BaseModel):
    """確認頁編輯器裡尚未存檔的 DDL 文字。"""

    ddl: str = Field(min_length=1, max_length=100_000)


class ValidateDDLTextResponse(BaseModel):
    """`checked` 為 "parse"（僅結構解析）或 "database"（已對真實資料庫試跑）。"""

    ok: bool
    error: str | None = None
    checked: str
    table_count: int | None = None
    warnings: list[str] = []


class DDLImportRequest(BaseModel):
    title: str | None = None
    ddl: str = Field(min_length=1, max_length=100_000)


class DDLImportResponse(BaseModel):
    id: uuid.UUID
    table_count: int


class DictionaryEntryRequest(BaseModel):
    """資料字典的一則註記：column 省略時代表整張表的說明。"""

    db_name: str
    table: str = Field(min_length=1, max_length=200)
    column: str | None = None
    note: str = Field(default="", max_length=2_000)
    owner: str = Field(default="", max_length=100)


class SavedQuestionRequest(BaseModel):
    """新增或更新一則常用問題（帶 id 即為更新）。"""

    db_name: str
    question: str = Field(min_length=1, max_length=500)
    sql: str = Field(min_length=1, max_length=10_000)
    id: str | None = None


class ApproveQuestionRequest(BaseModel):
    db_name: str
