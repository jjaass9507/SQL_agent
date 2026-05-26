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


def extract_schema(db_url: str, schema: str = "public") -> tuple[list[TableSpec], str]:
    """
    Connect to PostgreSQL, read all tables in the given schema.
    Returns (tables, error_message). On success error_message is "".
    """
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
        return [], f"schema '{schema}' 中未找到任何資料表"

    # Group rows by table
    tables_dict: dict[str, dict] = {}
    for row in rows:
        tname = row["table_name"]
        if tname not in tables_dict:
            tables_dict[tname] = {
                "description": row["table_comment"] or "",
                "columns": [],
            }
        ref = None
        if row["is_foreign_key"] and row["foreign_table_name"]:
            ref = f"{row['foreign_table_name']}.{row['foreign_column_name']}"
        tables_dict[tname]["columns"].append(ColumnSpec(
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

    tables = [
        TableSpec(
            table_name=name,
            description=info["description"],
            columns=info["columns"],
            constraints=[],
            related_tables=[],
        )
        for name, info in tables_dict.items()
    ]
    return tables, ""


def format_context(tables: list[TableSpec]) -> str:
    """Format tables as a compact text block for injecting into the Interviewer system prompt."""
    if not tables:
        return ""
    lines = [
        "--- 現有資料庫結構（供參考，設計新表時請注意關聯與命名一致性）---",
    ]
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
            lines.append(f"  {c.name}: {type_str}{flag_str}")
    lines.append("\n--- 現有結構結束 ---")
    return "\n".join(lines)
