"""Metadata completeness check for existing DB tables: table/column comment
coverage statistics, plus a COMMENT ON DDL drafter for filling in gaps.

Pure rule-based (no LLM connection needed to build the DDL text); the LLM
agent supplies the actual comment text by reasoning over column names/types,
then calls draft_comment_ddl() to turn that into safe, executable DDL.
"""
from app.rules.spec_models import TableSpec

# v0.5 derived this set from web.db_schema.platform_table_names() (the
# platform's own SQLAlchemy metadata + alembic_version). That module belongs
# to the repos layer, which rules/ must not import, so the bookkeeping-table
# names are inlined here; callers with a different platform schema can pass
# their own set via the hidden_tables parameter.
_PLATFORM_TABLE_NAMES = {
    "sessions", "messages", "activity_log", "change_requests", "alembic_version",
}


def check_metadata_completeness(existing_tables: list[TableSpec],
                                hidden_tables: set[str] | None = None) -> dict:
    """Return {"summary": {...}, "missing": [...]} describing how much of the
    existing DB has table/column comments, excluding platform tables."""
    hidden = {n.lower() for n in (hidden_tables if hidden_tables is not None
                                  else _PLATFORM_TABLE_NAMES)}
    tables = [t for t in existing_tables if t.table_name.lower() not in hidden]

    tables_total = len(tables)
    tables_without_comment = 0
    columns_total = 0
    columns_without_comment = 0
    missing: list[dict] = []

    for t in tables:
        table_comment_missing = not (t.description or "").strip()
        if table_comment_missing:
            tables_without_comment += 1
        columns_missing = [c.name for c in t.columns if not (c.description or "").strip()]
        columns_total += len(t.columns)
        columns_without_comment += len(columns_missing)
        if table_comment_missing or columns_missing:
            missing.append({
                "table": t.table_name,
                "table_comment_missing": table_comment_missing,
                "columns_missing": columns_missing,
            })

    total_items = tables_total + columns_total
    covered_items = ((tables_total - tables_without_comment)
                     + (columns_total - columns_without_comment))
    coverage_pct = round(covered_items / total_items * 100, 1) if total_items else 100.0

    return {
        "summary": {
            "tables_total": tables_total,
            "tables_without_comment": tables_without_comment,
            "columns_total": columns_total,
            "columns_without_comment": columns_without_comment,
            "coverage_pct": coverage_pct,
        },
        "missing": missing,
    }


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_qualified_table(table: str) -> str:
    """Quote each dot-separated part so `schema.table` (db_introspect's
    display name for non-public schemas) becomes "schema"."table"."""
    return ".".join(_quote_ident(part) for part in table.split("."))


def _escape_literal(text: str) -> str:
    return text.replace("'", "''")


def draft_comment_ddl(db: str, table: str, comments: dict) -> str:
    """Build COMMENT ON TABLE / COMMENT ON COLUMN statements from
    {"table_comment": "...", "columns": {"col": "..."}}.

    `db` is accepted for interface parity with the other agent tools
    (db, table, ...) — COMMENT ON runs against whichever connection executes
    it, so the DDL text itself doesn't need to reference the database name.
    """
    qualified_table = _quote_qualified_table(table)
    statements = []

    table_comment = (comments or {}).get("table_comment")
    if table_comment:
        statements.append(
            f"COMMENT ON TABLE {qualified_table} IS '{_escape_literal(table_comment)}';"
        )

    for col_name, comment in ((comments or {}).get("columns") or {}).items():
        if not comment:
            continue
        statements.append(
            f"COMMENT ON COLUMN {qualified_table}.{_quote_ident(col_name)} "
            f"IS '{_escape_literal(comment)}';"
        )

    return "\n".join(statements)
