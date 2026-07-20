"""對「使用者的業務資料庫」執行唯讀操作的共用模組。

供 SQL 工作台（workbench）與 DB Agent 工具共用，避免兩條路徑各自實作。
所有查詢先過 `rules.sql_safety.check_read_only`（單一 SELECT/EXPLAIN 護欄），
連線以唯讀 + statement timeout 開啟；同步 driver 一律包進 thread executor，
不阻塞 event loop。連線字串只進不出——錯誤訊息回傳前不得含 db_url。
"""

import asyncio
from typing import Any

from sqlalchemy import create_engine, text

from app.rules import sql_safety
from app.rules.db_introspect import _list_user_schemas, extract_schema
from app.rules.spec_models import TableSpec

MAX_ROWS = 200
_STATEMENT_TIMEOUT_MS = 30_000


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
    return await asyncio.to_thread(_run_sync, db_url, sql, max_rows)


async def explain_query(db_url: str, sql: str) -> dict[str, Any]:
    """回傳查詢的 EXPLAIN 計畫（不執行原查詢本體）。"""
    error = sql_safety.check_read_only(sql)
    if error:
        raise QueryRejected(error)
    return await asyncio.to_thread(_run_sync, db_url, f"EXPLAIN {sql}", MAX_ROWS)


async def schema_tree(db_url: str) -> tuple[list[TableSpec], str]:
    """擷取業務資料庫結構（委派 rules.db_introspect.extract_schema，預設 public schema）。"""
    return await asyncio.to_thread(extract_schema, db_url)


async def schema_overview(
    db_url: str, schema: str = "public"
) -> tuple[list[TableSpec], list[str], str]:
    """擷取指定 schema 的結構，並一併回傳資料庫裡所有可用 user schema 名稱。

    回傳 (tables, available_schemas, error)。供 DB Agent 的 get_schema 工具使用：
    讓模型/使用者看得到「還有哪些 schema」，並能以 schema 參數切換。
    """
    tables, err = await asyncio.to_thread(extract_schema, db_url, schema)
    available = await asyncio.to_thread(_list_user_schemas, db_url)
    return tables, available, err
