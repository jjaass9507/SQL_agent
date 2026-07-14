"""SQL 工作台的業務邏輯：唯讀查詢/EXPLAIN 護欄委派、結構瀏覽、NL2SQL、
DDL dry-run 驗證、貼上 DDL 建立設計 session。

一律不 import FastAPI；查詢類操作委派 `app.services.dbops`（唯一實作處），
不重複實作。connection string 只進不出：任何回傳給呼叫端的錯誤訊息都經過
`sanitize_db_error()` 去除可能夾帶的帳密。
"""

import asyncio
import re
import uuid

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.provider import LLMProvider
from app.llm.types import Message
from app.repos import activity, outputs, sessions, versions
from app.repos.crypto import decrypt_db_url
from app.repos.models import SessionRecord
from app.rules import ddl_parser, ddl_validator, sql_safety
from app.rules.db_introspect import format_context
from app.rules.spec_models import TableSpec, asdict
from app.services import dbops

_CRED_RE = re.compile(r"://[^\s/]+:[^\s/@]+@")

_NL2SQL_SYSTEM = (
    "你是 SQL 產生助手。根據使用者的自然語言問題與下方資料庫結構，"
    "產生一句唯讀的 PostgreSQL SELECT 查詢，並附上簡短說明。"
)


class SessionNotFound(Exception):
    """指定的 session 不存在。"""


class NoDatabaseConfigured(Exception):
    """session 尚未設定資料庫連線。"""


class SQLDraft(BaseModel):
    """NL2SQL structured output：LLM 產出的 SQL 草稿與說明（供 `response_model` 用）。"""

    sql: str
    explanation: str


def sanitize_db_error(text: str) -> str:
    """去除錯誤訊息中可能夾帶的連線字串帳密，避免外洩到前端或 log。"""
    return _CRED_RE.sub("://***:***@", text or "")[:500]


async def _get_session_or_raise(db: AsyncSession, session_id: uuid.UUID) -> SessionRecord:
    record = await sessions.get_session(db, session_id)
    if record is None:
        raise SessionNotFound(str(session_id))
    return record


async def _require_db_url(db: AsyncSession, session_id: uuid.UUID) -> str:
    record = await _get_session_or_raise(db, session_id)
    if not record.db_url_encrypted:
        raise NoDatabaseConfigured(str(session_id))
    return decrypt_db_url(record.db_url_encrypted)


async def run_query(db: AsyncSession, session_id: uuid.UUID, sql: str) -> dict:
    """對 session 的目標資料庫執行唯讀查詢（護欄不過拋 `dbops.QueryRejected`）。"""
    db_url = await _require_db_url(db, session_id)
    result = await dbops.execute_query(db_url, sql)
    await activity.log_activity(
        db, "query_executed", {"session_id": str(session_id), "rows": len(result["rows"])}
    )
    return result


async def run_explain(db: AsyncSession, session_id: uuid.UUID, sql: str) -> dict:
    """對 session 的目標資料庫執行 EXPLAIN。"""
    db_url = await _require_db_url(db, session_id)
    return await dbops.explain_query(db_url, sql)


def _table_to_tree(table: TableSpec) -> dict:
    """把 TableSpec 轉成結構瀏覽器用的精簡樹狀節點。"""
    columns = []
    for c in table.columns:
        columns.append(
            {
                "name": c.name,
                "type": f"{c.data_type}({c.length})" if c.length else c.data_type,
                "nullable": c.nullable,
                "is_pk": c.is_primary_key,
                "is_fk": c.is_foreign_key,
                "fk_table": c.references.split(".")[0] if c.references else None,
            }
        )
    return {"name": table.table_name, "columns": columns}


async def get_schema_tree(db: AsyncSession, session_id: uuid.UUID) -> dict:
    """session 有 db_url → 回傳實際 DB 結構；否則回傳設計中（最新版本快照）的 tables。"""
    record = await _get_session_or_raise(db, session_id)
    if record.db_url_encrypted:
        db_url = decrypt_db_url(record.db_url_encrypted)
        tables, _err = await dbops.schema_tree(db_url)
        if tables:
            return {"source": "db", "tables": [_table_to_tree(t) for t in tables]}
    latest = await versions.get_latest_version(db, session_id)
    raw_tables = (latest.tables_json if latest else None) or []
    designed = [TableSpec(**t) for t in raw_tables]
    return {"source": "design", "tables": [_table_to_tree(t) for t in designed]}


async def generate_nl2sql(
    db: AsyncSession, session_id: uuid.UUID, question: str, llm: LLMProvider
) -> SQLDraft:
    """依自然語言問題產生唯讀 SQL 草稿（不執行）。護欄不過拋 `dbops.QueryRejected`。"""
    db_url = await _require_db_url(db, session_id)
    tables, _err = await dbops.schema_tree(db_url)
    schema_summary = format_context(tables)
    messages: list[Message] = [
        {"role": "system", "content": _NL2SQL_SYSTEM},
        {"role": "user", "content": f"{schema_summary}\n\n問題：{question}"},
    ]
    result = await llm.chat(messages, response_model=SQLDraft)
    draft: SQLDraft = result.parsed
    error = sql_safety.check_read_only(draft.sql)
    if error:
        raise dbops.QueryRejected(error)
    await activity.log_activity(
        db, "nl2sql_generated", {"session_id": str(session_id), "q_len": len(question)}
    )
    return draft


async def validate_session_ddl(db: AsyncSession, session_id: uuid.UUID) -> dict:
    """把 session outputs 的 03_ddl.sql 交給 `rules.ddl_validator` 做 dry-run
    （同步呼叫丟 thread）。"""
    record = await _get_session_or_raise(db, session_id)
    ddl_output = await outputs.get_output(db, session_id, "03_ddl.sql")
    ddl_sql = (ddl_output.content if ddl_output else "") or ""
    if not ddl_sql.strip():
        return {"ok": False, "error": "尚未產生 DDL，請先完成文件產出"}
    if not record.db_url_encrypted:
        return {"ok": False, "error": "此 session 未設定資料庫連線，無法驗證 DDL"}
    conn_url = decrypt_db_url(record.db_url_encrypted)
    result = await asyncio.to_thread(ddl_validator.validate_ddl, ddl_sql, conn_url)
    if not result.get("ok"):
        result["error"] = sanitize_db_error(result.get("error", ""))
    await activity.log_activity(
        db, "ddl_validated", {"session_id": str(session_id), "ok": result.get("ok")}
    )
    return result


async def import_ddl(db: AsyncSession, title: str | None, ddl_text: str) -> dict:
    """貼上 CREATE TABLE 文字 → 解析 → 建立 design session（phase="confirming"）+ 版本快照。"""
    ddl_text = (ddl_text or "").strip()
    if not ddl_text:
        raise ValueError("ddl required")
    tables = ddl_parser.parse_ddl(ddl_text)
    if not tables:
        raise ValueError("未能解析出任何 CREATE TABLE 語句，請確認 DDL 格式")

    session_title = (title or "DDL 匯入設計").strip()[:120]
    record = await sessions.create_session(db, title=session_title, mode="design")
    await sessions.update_session(db, record.id, phase="confirming")

    tables_json = [asdict(t) for t in tables]
    await versions.create_version(
        db,
        record.id,
        tables_json=tables_json,
        key_points_json=[f"從 DDL 匯入 {len(tables)} 個資料表，可在此調整後產出文件"],
    )
    await activity.log_activity(
        db, "ddl_imported", {"session_id": str(record.id), "table_count": len(tables)}
    )
    return {"id": record.id, "table_count": len(tables)}
