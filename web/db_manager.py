"""
DB Management Agent — runs SELECT/EXPLAIN queries against a session's target database.
Security enforcement is centralised here; app.py routes only call these functions.
db_url is always sourced from the server-side session record, never from the frontend.
"""
import re
import logging

logger = logging.getLogger(__name__)

# Reject DML, DDL, and DCL
_FORBIDDEN_RE = re.compile(
    r"^\s*(CREATE|DROP|ALTER|TRUNCATE|GRANT|REVOKE|INSERT|UPDATE|DELETE|MERGE)\b",
    re.IGNORECASE,
)

QUERY_TIMEOUT_MS = 30_000


def _check_sql(sql: str) -> str | None:
    """Returns an error string if the SQL is forbidden, else None."""
    if not sql or not sql.strip():
        return "SQL query is empty"
    if _FORBIDDEN_RE.match(sql.strip()):
        return "Only SELECT and EXPLAIN queries are allowed"
    return None


def execute_query(db_url: str, sql: str, limit: int = 500) -> dict:
    """
    Execute a read-only SQL query against db_url.
    Returns {"columns": [...], "rows": [[...], ...], "row_count": N, "truncated": bool}
    or {"error": "..."}.
    """
    err = _check_sql(sql)
    if err:
        return {"error": err}
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        return {"error": "psycopg2-binary is not installed"}
    try:
        conn = psycopg2.connect(db_url, connect_timeout=10)
    except Exception as exc:
        return {"error": f"連線失敗：{str(exc)[:200]}"}
    try:
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SET statement_timeout = {QUERY_TIMEOUT_MS}")
            cur.execute(sql)
            rows_raw = cur.fetchmany(limit + 1)
            truncated = len(rows_raw) > limit
            rows_raw = rows_raw[:limit]
            if rows_raw:
                columns = list(rows_raw[0].keys())
                rows = [list(r.values()) for r in rows_raw]
            else:
                columns = [desc[0] for desc in cur.description] if cur.description else []
                rows = []
        return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated}
    except Exception as exc:
        logger.warning("execute_query error: %s", exc)
        return {"error": str(exc)[:300]}
    finally:
        conn.close()


def list_tables(db_url: str, schema: str = "public") -> dict:
    """Returns {"tables": [...]} or {"error": "..."}."""
    sql = """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = %s AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=10)
        with conn.cursor() as cur:
            cur.execute(sql, (schema,))
            tables = [row[0] for row in cur.fetchall()]
        conn.close()
        return {"tables": tables}
    except Exception as exc:
        return {"error": str(exc)[:300]}


def get_table_ddl(db_url: str, table_name: str, schema: str = "public") -> dict:
    """Returns {"ddl": "CREATE TABLE ..."} or {"error": "..."}."""
    sql = """
        SELECT column_name, data_type, character_maximum_length,
               is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
    """
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=10)
        with conn.cursor() as cur:
            cur.execute(sql, (schema, table_name))
            cols = cur.fetchall()
        conn.close()
        if not cols:
            return {"error": f"Table '{table_name}' not found in schema '{schema}'"}
        lines = []
        for col in cols:
            cname, dtype, maxlen, nullable, default = col
            type_str = f"{dtype}({maxlen})" if maxlen else dtype
            null_str = "" if nullable == "YES" else " NOT NULL"
            def_str = f" DEFAULT {default}" if default else ""
            lines.append(f"  {cname} {type_str}{null_str}{def_str}")
        ddl = f"CREATE TABLE {schema}.{table_name} (\n" + ",\n".join(lines) + "\n);"
        return {"ddl": ddl}
    except Exception as exc:
        return {"error": str(exc)[:300]}


def explain_query(db_url: str, sql: str) -> dict:
    """Returns {"plan": "..."} or {"error": "..."}."""
    err = _check_sql(sql)
    if err:
        return {"error": err}
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=10)
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {QUERY_TIMEOUT_MS}")
            cur.execute(f"EXPLAIN {sql}")
            plan_rows = cur.fetchall()
        conn.close()
        return {"plan": "\n".join(row[0] for row in plan_rows)}
    except Exception as exc:
        return {"error": str(exc)[:300]}
