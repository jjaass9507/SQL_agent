"""
DB Management Agent — runs SELECT/EXPLAIN queries against a session's target database.
Security enforcement is centralised here; app.py routes only call these functions.
db_url is always sourced from the server-side session record, never from the frontend.
"""
import logging

from web.sql_safety import check_read_only

logger = logging.getLogger(__name__)

QUERY_TIMEOUT_MS = 30_000


def _check_sql(sql: str) -> str | None:
    """Returns an error string if the SQL is forbidden, else None."""
    return check_read_only(sql)


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
        conn = psycopg2.connect(
            db_url, connect_timeout=10,
            options=f"-c statement_timeout={QUERY_TIMEOUT_MS}",
        )
    except Exception as exc:
        return {"error": f"連線失敗：{str(exc)[:200]}"}
    try:
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
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


def schema_tree(db_url: str, schema: str | None = "public") -> dict:
    """
    Return tables + columns (with PK/FK flags) for the schema browser.
    schema=None → all non-system schemas; non-public tables shown as "schema.table".
    Returns {"tables": [...]} or {"error": "..."}.
    """
    from web.db_introspect import _list_user_schemas

    schemas_to_query = [schema] if schema is not None else _list_user_schemas(db_url)

    sql = """
        SELECT c.table_name, c.column_name, c.data_type,
               c.character_maximum_length, c.is_nullable,
               (pk.column_name IS NOT NULL) AS is_pk,
               (fk.column_name IS NOT NULL) AS is_fk,
               fk.foreign_table_name
        FROM information_schema.columns c
        JOIN information_schema.tables t
             ON t.table_schema = c.table_schema AND t.table_name = c.table_name
            AND t.table_type = 'BASE TABLE'
        LEFT JOIN (
            SELECT kcu.table_name, kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                 ON kcu.constraint_name = tc.constraint_name
                AND kcu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = %(schema)s
        ) pk ON pk.table_name = c.table_name AND pk.column_name = c.column_name
        LEFT JOIN (
            SELECT kcu.table_name, kcu.column_name,
                   ccu.table_name AS foreign_table_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                 ON kcu.constraint_name = tc.constraint_name
                AND kcu.table_schema = tc.table_schema
            JOIN information_schema.constraint_column_usage ccu
                 ON ccu.constraint_name = tc.constraint_name
            WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = %(schema)s
        ) fk ON fk.table_name = c.table_name AND fk.column_name = c.column_name
        WHERE c.table_schema = %(schema)s
        ORDER BY c.table_name, c.ordinal_position
    """

    all_tables: dict = {}
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=10)
        with conn.cursor() as cur:
            for s in schemas_to_query:
                cur.execute(sql, {"schema": s})
                rows = cur.fetchall()
                for tname, cname, dtype, maxlen, nullable, is_pk, is_fk, fk_table in rows:
                    display_name = f"{s}.{tname}" if s != "public" else tname
                    t = all_tables.setdefault(display_name, {"name": display_name, "columns": []})
                    type_str = f"{dtype}({maxlen})" if maxlen else dtype
                    t["columns"].append({
                        "name": cname,
                        "type": type_str,
                        "nullable": nullable == "YES",
                        "is_pk": bool(is_pk),
                        "is_fk": bool(is_fk),
                        "fk_table": fk_table,
                    })
        conn.close()
    except Exception as exc:
        return {"error": str(exc)[:300]}

    return {"tables": list(all_tables.values())}


def explain_query(db_url: str, sql: str) -> dict:
    """Returns {"plan": "..."} or {"error": "..."}."""
    err = _check_sql(sql)
    if err:
        return {"error": err}
    try:
        import psycopg2
        conn = psycopg2.connect(
            db_url, connect_timeout=10,
            options=f"-c statement_timeout={QUERY_TIMEOUT_MS}",
        )
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor() as cur:
            cur.execute(f"EXPLAIN {sql}")
            plan_rows = cur.fetchall()
        conn.close()
        return {"plan": "\n".join(row[0] for row in plan_rows)}
    except Exception as exc:
        return {"error": str(exc)[:300]}
