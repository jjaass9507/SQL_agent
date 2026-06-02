"""Natural-language → SQL for the SQL Workbench.

Generates a single read-only SELECT from a natural-language question and the
target schema, then validates it through the same guardrail the workbench uses
(web.db_manager._check_sql) so only SELECT/EXPLAIN can ever come out.
"""
import re

from utils.client import get_api
from web.db_manager import _check_sql

_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def format_schema(tables: list[dict]) -> str:
    """Compact schema text from schema_tree() output, fed to the LLM as context."""
    lines = []
    for t in tables:
        cols = []
        for c in t.get("columns", []):
            flags = []
            if c.get("is_pk"):
                flags.append("PK")
            if c.get("is_fk"):
                flags.append(f"FK->{c.get('fk_table', '')}")
            suffix = f" [{', '.join(flags)}]" if flags else ""
            cols.append(f"{c.get('name')} {c.get('type', '')}{suffix}")
        lines.append(f"{t.get('name')}({', '.join(cols)})")
    return "\n".join(lines)


def _extract_sql(text: str) -> str:
    if not text:
        return ""
    m = _FENCE_RE.search(text)
    sql = (m.group(1) if m else text).strip()
    return sql.rstrip(";").strip()


def generate_sql(question: str, schema_text: str) -> dict:
    """Return {"sql": ...} or {"error": ...}."""
    if not question or not question.strip():
        return {"error": "問題不可為空"}
    system_prompt = (
        "你是 PostgreSQL 專家。根據提供的資料庫結構，把使用者的自然語言問題轉成"
        "**單一個唯讀 SELECT 查詢**（PostgreSQL 語法）。\n"
        "規則：\n"
        "- 只能用 SELECT，嚴禁 INSERT / UPDATE / DELETE / DDL / DCL。\n"
        "- 除非問題本身是彙總，否則加上合理的 LIMIT（例如 100）避免回傳過多列。\n"
        "- 只輸出 SQL 本身，不要任何解說文字或 markdown 標記。\n\n"
        f"資料庫結構：\n{schema_text}"
    )
    response = get_api().chat(system_prompt=system_prompt, human_prompt=question.strip())
    sql = _extract_sql(response or "")
    if not sql:
        return {"error": "無法產生 SQL，請換個說法再試"}
    err = _check_sql(sql)
    if err:
        return {"error": f"產生的並非唯讀查詢（{err}），請調整問題後再試"}
    return {"sql": sql}
