"""Execute pre-validated DDL against a target PostgreSQL database.

This module is intentionally separate from db_manager.py (which is read-only).
All callers must pass DDL that has already been checked by ddl_guard.check_ddl_safety().
"""
import logging

logger = logging.getLogger(__name__)

DDL_TIMEOUT_MS = 30_000


def execute_ddl(db_url: str, ddl: str) -> dict:
    """Execute one or more DDL statements against db_url.

    Returns {"ok": True, "statements_run": N} or {"ok": False, "error": "..."}.
    Caller is responsible for calling ddl_guard.check_ddl_safety() first.
    """
    try:
        import psycopg2
    except ImportError:
        return {"ok": False, "error": "psycopg2-binary is not installed"}

    try:
        conn = psycopg2.connect(
            db_url,
            connect_timeout=10,
            options=f"-c statement_timeout={DDL_TIMEOUT_MS}",
        )
    except Exception as exc:
        return {"ok": False, "error": f"連線失敗：{str(exc)[:200]}"}

    conn.autocommit = True
    statements_run = 0
    try:
        with conn.cursor() as cur:
            for stmt in ddl.split(";"):
                stmt = stmt.strip()
                if not stmt:
                    continue
                cur.execute(stmt)
                statements_run += 1
    except Exception as exc:
        logger.warning("ddl_executor: statement failed", extra={"err": str(exc)[:300]})
        return {"ok": False, "error": str(exc)[:300]}
    finally:
        conn.close()

    logger.info("ddl_executor: executed %d statements", statements_run)
    return {"ok": True, "statements_run": statements_run}
