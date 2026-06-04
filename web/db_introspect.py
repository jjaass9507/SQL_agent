"""
Extract existing table schema from a PostgreSQL database.
Returns a list of TableSpec and a formatted context string for the Interviewer.
"""
from models.schema import ColumnSpec, TableSpec

_COLS_QUERY = """
SELECT
    c.table_name,
    c.column_name,
    c.data_type,
    c.character_maximum_length,
    c.is_nullable,
    c.column_default,
    CASE WHEN pk.column_name IS NOT NULL THEN true ELSE false END AS is_primary_key,
    CASE WHEN fk.column_name IS NOT NULL THEN true ELSE false END AS is_foreign_key,
    fk.foreign_table_name,
    fk.foreign_column_name,
    CASE WHEN uq.column_name IS NOT NULL THEN true ELSE false END AS is_unique,
    CASE WHEN ix.column_name IS NOT NULL THEN true ELSE false END AS is_indexed,
    obj_description(t.oid, 'pg_class') AS table_comment,
    pg_catalog.col_description(t.oid, c.ordinal_position) AS column_comment
FROM information_schema.columns c
JOIN information_schema.tables tbl
    ON tbl.table_name = c.table_name AND tbl.table_schema = c.table_schema
LEFT JOIN pg_catalog.pg_class t
    ON t.relname = c.table_name
    AND t.relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = c.table_schema)
LEFT JOIN (
    SELECT ku.table_name, ku.column_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage ku
        ON tc.constraint_name = ku.constraint_name AND tc.table_schema = ku.table_schema
    WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = %(schema)s
) pk ON pk.table_name = c.table_name AND pk.column_name = c.column_name
LEFT JOIN (
    SELECT ku.table_name, ku.column_name,
           ccu.table_name AS foreign_table_name, ccu.column_name AS foreign_column_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage ku
        ON tc.constraint_name = ku.constraint_name AND tc.table_schema = ku.table_schema
    JOIN information_schema.constraint_column_usage ccu
        ON tc.constraint_name = ccu.constraint_name AND tc.table_schema = ccu.table_schema
    WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = %(schema)s
) fk ON fk.table_name = c.table_name AND fk.column_name = c.column_name
LEFT JOIN (
    SELECT ku.table_name, ku.column_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage ku
        ON tc.constraint_name = ku.constraint_name AND tc.table_schema = ku.table_schema
    WHERE tc.constraint_type = 'UNIQUE' AND tc.table_schema = %(schema)s
) uq ON uq.table_name = c.table_name AND uq.column_name = c.column_name
LEFT JOIN (
    SELECT t2.relname AS table_name, a.attname AS column_name
    FROM pg_index ix
    JOIN pg_class t2 ON t2.oid = ix.indrelid
    JOIN pg_attribute a ON a.attrelid = t2.oid AND a.attnum = ANY(ix.indkey)
    JOIN pg_namespace n ON n.oid = t2.relnamespace
    WHERE NOT ix.indisprimary AND NOT ix.indisunique AND n.nspname = %(schema)s
) ix ON ix.table_name = c.table_name AND ix.column_name = c.column_name
WHERE c.table_schema = %(schema)s
    AND tbl.table_type = 'BASE TABLE'
ORDER BY c.table_name, c.ordinal_position
"""


_LIST_SCHEMAS_QUERY = """
SELECT schema_name FROM information_schema.schemata
WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  AND schema_name NOT LIKE 'pg\_%'
ORDER BY schema_name
"""


def _list_user_schemas(db_url: str) -> list[str]:
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=10)
        with conn.cursor() as cur:
            cur.execute(_LIST_SCHEMAS_QUERY)
            schemas = [row[0] for row in cur.fetchall()]
        conn.close()
        return schemas
    except Exception:
        return ["public"]


def _extract_one(db_url: str, schema: str) -> tuple[list[TableSpec], str]:
    """Extract all tables from a single schema. Returns (tables, error_msg)."""
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        return [], "缺少 psycopg2-binary，請執行 pip install psycopg2-binary"

    try:
        conn = psycopg2.connect(db_url, connect_timeout=10)
    except Exception as e:
        return [], f"連線失敗：{e}"

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(_COLS_QUERY, {"schema": schema})
            rows = cur.fetchall()
    except Exception as e:
        conn.close()
        return [], f"查詢失敗：{e}"
    finally:
        conn.close()

    if not rows:
        return [], ""

    tables_dict: dict[str, dict] = {}
    for row in rows:
        tname = row["table_name"]
        # Prefix non-public schema tables as "schema.table"
        display_name = f"{schema}.{tname}" if schema != "public" else tname
        if display_name not in tables_dict:
            tables_dict[display_name] = {
                "description": row["table_comment"] or "",
                "columns": [],
            }
        ref = None
        if row["is_foreign_key"] and row["foreign_table_name"]:
            ref = f"{row['foreign_table_name']}.{row['foreign_column_name']}"
        tables_dict[display_name]["columns"].append(ColumnSpec(
            name=row["column_name"],
            data_type=row["data_type"],
            length=row["character_maximum_length"],
            nullable=(row["is_nullable"] == "YES"),
            default=row["column_default"],
            description=row["column_comment"] or "",
            is_primary_key=bool(row["is_primary_key"]),
            is_foreign_key=bool(row["is_foreign_key"]),
            references=ref,
            is_unique=bool(row["is_unique"]),
            is_indexed=bool(row["is_indexed"]),
        ))

    return [
        TableSpec(
            table_name=name,
            description=info["description"],
            columns=info["columns"],
            constraints=[],
            related_tables=[],
        )
        for name, info in tables_dict.items()
    ], ""


def extract_schema(db_url: str, schema: str | None = "public") -> tuple[list[TableSpec], str]:
    """
    Connect to PostgreSQL and read tables.
    schema=None → all non-system schemas; non-public tables prefixed as "schema.table".
    schema="public" (or any value) → original single-schema behaviour.
    Returns (tables, error_message). On success error_message is "".
    """
    if schema is not None:
        tables, err = _extract_one(db_url, schema)
        if err and not tables:
            return [], err
        if not tables:
            return [], f"schema '{schema}' 中未找到任何資料表"
        return tables, ""

    # All-schemas mode
    schemas = _list_user_schemas(db_url)
    all_tables: list[TableSpec] = []
    last_err = ""
    for s in schemas:
        tbls, err = _extract_one(db_url, s)
        if err:
            last_err = err
        all_tables.extend(tbls)
    if not all_tables:
        return [], last_err or "資料庫中未找到任何資料表"
    return all_tables, ""


def format_context(tables: list[TableSpec]) -> str:
    """Format tables for the Interviewer system prompt. Detail level scales with table count."""
    if not tables:
        return ""
    n = len(tables)
    lines = ["--- 現有資料庫結構（供參考，設計新表時請注意關聯與命名一致性）---"]

    if n <= 10:
        # Full: all columns with types and flags
        for t in tables:
            lines.append(f"\n【{t.table_name}】{' — ' + t.description if t.description else ''}")
            for c in t.columns:
                flags = []
                if c.is_primary_key:
                    flags.append("PK")
                if c.is_foreign_key and c.references:
                    flags.append(f"FK→{c.references}")
                if c.is_unique:
                    flags.append("UNIQUE")
                flag_str = f" ({', '.join(flags)})" if flags else ""
                type_str = f"{c.data_type}({c.length})" if c.length else c.data_type
                desc_str = f" — {c.description}" if c.description else ""
                lines.append(f"  {c.name}: {type_str}{flag_str}{desc_str}")
    elif n <= 30:
        # Compact: only PK/FK/UNIQUE columns; skip plain ones
        for t in tables:
            lines.append(f"\n【{t.table_name}】{' — ' + t.description if t.description else ''} ({len(t.columns)} 欄)")
            for c in t.columns:
                if not (c.is_primary_key or c.is_foreign_key or c.is_unique):
                    continue
                flags = []
                if c.is_primary_key:
                    flags.append("PK")
                if c.is_foreign_key and c.references:
                    flags.append(f"FK→{c.references}")
                if c.is_unique:
                    flags.append("UNIQUE")
                type_str = f"{c.data_type}({c.length})" if c.length else c.data_type
                desc_str = f" — {c.description}" if c.description else ""
                lines.append(f"  {c.name}: {type_str} ({', '.join(flags)}){desc_str}")
    else:
        # Ultra-compact: table name + column count + FK targets only
        for t in tables:
            fks = [f"{c.name}→{c.references}" for c in t.columns if c.is_foreign_key and c.references]
            fk_str = f", FK: {', '.join(fks)}" if fks else ""
            lines.append(f"  {t.table_name} ({len(t.columns)} 欄{fk_str})")

    lines.append("\n--- 現有結構結束 ---")
    return "\n".join(lines)
