"""
Extract existing table schema from a PostgreSQL database.
Returns a list of TableSpec and a formatted context string for the Interviewer.
"""
from app.rules.spec_models import ColumnSpec, TableSpec

_COLS_QUERY = r"""
WITH pk AS (
    SELECT tc.table_schema, ku.table_name, ku.column_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage ku
      ON tc.constraint_name = ku.constraint_name
     AND tc.constraint_schema = ku.constraint_schema
    WHERE tc.constraint_type = 'PRIMARY KEY'
),
fk AS (
    SELECT tc.table_schema, ku.table_name, ku.column_name,
           ccu.table_schema AS foreign_table_schema,
           ccu.table_name AS foreign_table_name,
           ccu.column_name AS foreign_column_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage ku
      ON tc.constraint_name = ku.constraint_name
     AND tc.constraint_schema = ku.constraint_schema
    JOIN information_schema.constraint_column_usage ccu
      ON tc.constraint_name = ccu.constraint_name
     AND tc.constraint_schema = ccu.constraint_schema
    WHERE tc.constraint_type = 'FOREIGN KEY'
),
uq AS (
    SELECT tc.table_schema, ku.table_name, ku.column_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage ku
      ON tc.constraint_name = ku.constraint_name
     AND tc.constraint_schema = ku.constraint_schema
    WHERE tc.constraint_type = 'UNIQUE'
),
ix AS (
    SELECT DISTINCT n.nspname AS table_schema,
           t2.relname AS table_name,
           a.attname AS column_name
    FROM pg_index i
    JOIN pg_class t2 ON t2.oid = i.indrelid
    JOIN pg_attribute a ON a.attrelid = t2.oid AND a.attnum = ANY(i.indkey)
    JOIN pg_namespace n ON n.oid = t2.relnamespace
    WHERE NOT i.indisprimary AND NOT i.indisunique
)
SELECT
    c.table_schema,
    c.table_name,
    c.column_name,
    c.data_type,
    c.character_maximum_length,
    c.is_nullable,
    c.column_default,
    (pk.column_name IS NOT NULL) AS is_primary_key,
    (fk.column_name IS NOT NULL) AS is_foreign_key,
    fk.foreign_table_schema,
    fk.foreign_table_name,
    fk.foreign_column_name,
    (uq.column_name IS NOT NULL) AS is_unique,
    (ix.column_name IS NOT NULL) AS is_indexed,
    obj_description(t.oid, 'pg_class') AS table_comment,
    pg_catalog.col_description(t.oid, c.ordinal_position) AS column_comment
FROM information_schema.columns c
JOIN information_schema.tables tbl
  ON tbl.table_name = c.table_name AND tbl.table_schema = c.table_schema
LEFT JOIN pg_catalog.pg_class t
  ON t.relname = c.table_name
 AND t.relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = c.table_schema)
LEFT JOIN pk
  ON pk.table_schema = c.table_schema
 AND pk.table_name = c.table_name
 AND pk.column_name = c.column_name
LEFT JOIN fk
  ON fk.table_schema = c.table_schema
 AND fk.table_name = c.table_name
 AND fk.column_name = c.column_name
LEFT JOIN uq
  ON uq.table_schema = c.table_schema
 AND uq.table_name = c.table_name
 AND uq.column_name = c.column_name
LEFT JOIN ix
  ON ix.table_schema = c.table_schema
 AND ix.table_name = c.table_name
 AND ix.column_name = c.column_name
WHERE tbl.table_type = 'BASE TABLE'
  AND c.table_schema NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  AND c.table_schema NOT LIKE 'pg\_%' ESCAPE '\'
  AND (%(schema)s IS NULL OR c.table_schema = %(schema)s)
ORDER BY c.table_schema, c.table_name, c.ordinal_position
"""


def extract_schema(db_url: str, schema: str | None = None) -> tuple[list[TableSpec], str]:
    """Read one schema or every accessible non-system schema in one connection/query."""
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        return [], "缺少 psycopg2-binary，請執行 pip install psycopg2-binary"

    try:
        conn = psycopg2.connect(db_url, connect_timeout=10)
    except Exception as exc:
        return [], f"連線失敗：{exc}"

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(_COLS_QUERY, {"schema": schema})
            rows = cur.fetchall()
    except Exception as exc:
        return [], f"查詢失敗：{exc}"
    finally:
        conn.close()

    if not rows:
        scope = f"schema '{schema}'" if schema else "資料庫"
        return [], f"{scope} 中未找到任何資料表"

    tables_dict: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["table_schema"], row["table_name"])
        if key not in tables_dict:
            tables_dict[key] = {
                "description": row["table_comment"] or "",
                "columns": [],
            }
        seen = {column.name for column in tables_dict[key]["columns"]}
        if row["column_name"] in seen:
            continue

        ref_schema = row["foreign_table_schema"] if row["is_foreign_key"] else None
        ref_table = row["foreign_table_name"] if row["is_foreign_key"] else None
        ref_column = row["foreign_column_name"] if row["is_foreign_key"] else None
        # Keep the legacy two-part value for old consumers; new code uses the
        # structured reference_* fields and therefore supports cross-schema FKs.
        legacy_ref = f"{ref_table}.{ref_column}" if ref_table and ref_column else None
        tables_dict[key]["columns"].append(ColumnSpec(
            name=row["column_name"],
            data_type=row["data_type"],
            length=row["character_maximum_length"],
            nullable=(row["is_nullable"] == "YES"),
            default=row["column_default"],
            description=row["column_comment"] or "",
            is_primary_key=bool(row["is_primary_key"]),
            is_foreign_key=bool(row["is_foreign_key"]),
            references=legacy_ref,
            reference_schema=ref_schema,
            reference_table=ref_table,
            reference_column=ref_column,
            is_unique=bool(row["is_unique"]),
            is_indexed=bool(row["is_indexed"]),
        ))

    return [
        TableSpec(
            schema_name=schema_name,
            table_name=table_name,
            description=info["description"],
            columns=info["columns"],
            constraints=[],
            related_tables=[],
        )
        for (schema_name, table_name), info in tables_dict.items()
    ], ""


def _reference_name(column: ColumnSpec) -> str | None:
    if column.reference_table and column.reference_column:
        schema = column.reference_schema or "public"
        return f"{schema}.{column.reference_table}.{column.reference_column}"
    return column.references


def format_context(tables: list[TableSpec]) -> str:
    """Format schema-qualified tables for prompts. Detail scales with table count."""
    if not tables:
        return ""
    n = len(tables)
    lines = ["--- 現有資料庫結構（SQL 請使用 schema-qualified 表名）---"]

    if n <= 10:
        for table in tables:
            title = f" — {table.description}" if table.description else ""
            lines.append(f"\n【{table.qualified_name}】{title}")
            for column in table.columns:
                flags = []
                if column.is_primary_key:
                    flags.append("PK")
                reference = _reference_name(column)
                if column.is_foreign_key and reference:
                    flags.append(f"FK→{reference}")
                if column.is_unique:
                    flags.append("UNIQUE")
                flag_str = f" ({', '.join(flags)})" if flags else ""
                type_str = (
                    f"{column.data_type}({column.length})"
                    if column.length else column.data_type
                )
                desc_str = f" — {column.description}" if column.description else ""
                lines.append(f"  {column.name}: {type_str}{flag_str}{desc_str}")
    elif n <= 30:
        for table in tables:
            title = f" — {table.description}" if table.description else ""
            lines.append(f"\n【{table.qualified_name}】{title} ({len(table.columns)} 欄)")
            for column in table.columns:
                if not (column.is_primary_key or column.is_foreign_key or column.is_unique):
                    continue
                flags = []
                if column.is_primary_key:
                    flags.append("PK")
                reference = _reference_name(column)
                if column.is_foreign_key and reference:
                    flags.append(f"FK→{reference}")
                if column.is_unique:
                    flags.append("UNIQUE")
                type_str = (
                    f"{column.data_type}({column.length})"
                    if column.length else column.data_type
                )
                desc_str = f" — {column.description}" if column.description else ""
                lines.append(
                    f"  {column.name}: {type_str} ({', '.join(flags)}){desc_str}"
                )
    else:
        for table in tables:
            fks = [
                f"{column.name}→{_reference_name(column)}"
                for column in table.columns
                if column.is_foreign_key and _reference_name(column)
            ]
            fk_str = f", FK: {', '.join(fks)}" if fks else ""
            lines.append(
                f"  {table.qualified_name} ({len(table.columns)} 欄{fk_str})"
            )

    lines.append("\n--- 現有結構結束 ---")
    return "\n".join(lines)
