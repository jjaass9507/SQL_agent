"""DDL dry-run validation.

Applies the generated DDL inside a throwaway temporary schema within a
transaction that is always rolled back, so syntax / type / dependency errors
surface against a real PostgreSQL without leaving anything behind.
"""
import logging
import uuid

logger = logging.getLogger(__name__)

VALIDATE_TIMEOUT_MS = 30_000


def validate_ddl(ddl_sql: str, conn_url: str) -> dict:
    """Run ddl_sql in a rolled-back temp schema. Returns {"ok": True} or
    {"ok": False, "error": "..."}."""
    if not ddl_sql or not ddl_sql.strip():
        return {"ok": False, "error": "DDL 內容為空"}
    if not conn_url:
        return {"ok": False, "error": "沒有可用的資料庫連線"}
    try:
        import psycopg2
    except ImportError:
        return {"ok": False, "error": "psycopg2-binary is not installed"}

    try:
        conn = psycopg2.connect(
            conn_url, connect_timeout=10,
            options=f"-c statement_timeout={VALIDATE_TIMEOUT_MS}",
        )
    except Exception as exc:
        return {"ok": False, "error": f"連線失敗：{str(exc)[:200]}"}

    tmp = f"_sqlagent_dryrun_{uuid.uuid4().hex[:12]}"
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            # Isolate in a temp schema (search_path keeps public for extensions/types)
            cur.execute(f'CREATE SCHEMA "{tmp}"; SET LOCAL search_path TO "{tmp}", public;')
            cur.execute(ddl_sql)
        conn.rollback()  # undo everything, including the temp schema
        return {"ok": True}
    except Exception as exc:
        conn.rollback()
        logger.info("ddl validation failed: %s", str(exc)[:200])
        return {"ok": False, "error": str(exc).strip()[:400]}
    finally:
        conn.close()
