"""對「使用者的業務資料庫」執行唯讀操作的共用模組。

供 SQL 工作台（workbench）與 DB Agent 工具共用，避免兩條路徑各自實作。
所有查詢先過 `rules.sql_safety.check_read_only`（單一 SELECT/EXPLAIN 護欄），
連線以唯讀 + statement timeout 開啟；同步 driver 一律包進 thread executor，
不阻塞 event loop。連線字串只進不出——錯誤訊息回傳前不得含 db_url。
"""

import asyncio
import os
from typing import Any

from sqlalchemy import create_engine, text

from app.rules import sql_safety
from app.rules.db_introspect import extract_schema, list_table_refs
from app.rules.spec_models import TableSpec

MAX_ROWS = 200
_STATEMENT_TIMEOUT_MS = 30_000

# 同時間最多幾個查詢打到業務資料庫。每次查詢都會新開連線（用完即 dispose），
# 沒有上限的話，多人同時操作工作台／DB Agent 會把正式庫的 max_connections 頂滿，
# 導致該資料庫上其他正式服務出現 too many connections。
# 30 秒的 statement_timeout 對「短查詢但很多人同時打」完全無效。
MAX_CONCURRENT_QUERIES = int(os.environ.get("MAX_CONCURRENT_QUERIES", "10"))

_query_slots = asyncio.Semaphore(MAX_CONCURRENT_QUERIES)


class QueryRejected(ValueError):
    """SQL 未通過唯讀護欄。"""


def _connect_args(db_url: str) -> dict:
    if db_url.startswith("postgresql"):
        opts = f"-c statement_timeout={_STATEMENT_TIMEOUT_MS} -c default_transaction_read_only=on"
        return {"options": opts}
    return {}


def _run_sync(db_url: str, sql: str, max_rows: int) -> dict[str, Any]:
    engine = create_engine(db_url, connect_args=_connect_args(db_url), pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            result = conn.execute(text(sql))
            if not result.returns_rows:
                return {"columns": [], "rows": [], "truncated": False}
            columns = list(result.keys())
            fetched = result.fetchmany(max_rows + 1)
            truncated = len(fetched) > max_rows
            rows = [[_jsonable(v) for v in row] for row in fetched[:max_rows]]
            return {"columns": columns, "rows": rows, "truncated": truncated}
    finally:
        engine.dispose()


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


async def execute_query(db_url: str, sql: str, max_rows: int = MAX_ROWS) -> dict[str, Any]:
    """執行單一唯讀查詢，回傳 {columns, rows, truncated}。護欄不過即 raise QueryRejected。"""
    error = sql_safety.check_read_only(sql)
    if error:
        raise QueryRejected(error)
    async with _query_slots:
        return await asyncio.to_thread(_run_sync, db_url, sql, max_rows)


async def explain_query(db_url: str, sql: str) -> dict[str, Any]:
    """回傳查詢的 EXPLAIN 計畫（不執行原查詢本體）。"""
    error = sql_safety.check_read_only(sql)
    if error:
        raise QueryRejected(error)
    async with _query_slots:
        return await asyncio.to_thread(_run_sync, db_url, f"EXPLAIN {sql}", MAX_ROWS)


async def list_tables(
    db_url: str,
    schema: str | None = None,
    name_contains: str | None = None,
) -> tuple[list[tuple[str, str]], str]:
    """輕量列出 schema/table；不讀欄位、constraint、index 或 comment。"""
    return await asyncio.to_thread(list_table_refs, db_url, schema, name_contains)


async def list_schemas(db_url: str) -> tuple[list[str], str]:
    """列出至少含一張可存取資料表的 schema。"""
    refs, err = await list_tables(db_url)
    return sorted({schema for schema, _table in refs}), err


async def schema_tree(
    db_url: str,
    schema: str | None = None,
    table_names: list[str] | None = None,
) -> tuple[list[TableSpec], str]:
    """擷取完整結構；呼叫端可縮到單一 schema 與指定資料表。"""
    if schema is None and table_names is None:
        # 保留既有呼叫／monkeypatch 契約。
        return await asyncio.to_thread(extract_schema, db_url)
    return await asyncio.to_thread(extract_schema, db_url, schema, table_names)
