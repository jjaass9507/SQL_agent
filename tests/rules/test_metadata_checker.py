"""Tests for app/rules/metadata_checker.py — comment coverage stats + COMMENT ON drafting."""
from app.rules.metadata_checker import check_metadata_completeness, draft_comment_ddl
from app.rules.spec_models import ColumnSpec, TableSpec
from app.rules.sql_safety import check_ddl_allowlist


def _col(name, description=""):
    return ColumnSpec(name=name, data_type="text", nullable=True, description=description)


def test_summary_counts_missing_comments():
    tables = [
        TableSpec(table_name="orders", description="訂單主檔", columns=[
            _col("id", "主鍵"),
            _col("status", ""),
        ]),
        TableSpec(table_name="products", description="", columns=[
            _col("id", ""),
        ]),
    ]
    result = check_metadata_completeness(tables)
    assert result["summary"]["tables_total"] == 2
    assert result["summary"]["tables_without_comment"] == 1
    assert result["summary"]["columns_total"] == 3
    assert result["summary"]["columns_without_comment"] == 2
    missing_tables = {m["table"] for m in result["missing"]}
    assert missing_tables == {"orders", "products"}
    orders_missing = next(m for m in result["missing"] if m["table"] == "orders")
    assert orders_missing["table_comment_missing"] is False
    assert orders_missing["columns_missing"] == ["status"]


def test_platform_bookkeeping_tables_excluded():
    tables = [
        TableSpec(table_name="sessions", description="", columns=[_col("id")]),
        TableSpec(table_name="messages", description="", columns=[_col("id")]),
        TableSpec(table_name="activity_log", description="", columns=[_col("id")]),
        TableSpec(table_name="alembic_version", description="", columns=[_col("version_num")]),
        TableSpec(table_name="orders", description="訂單", columns=[_col("id", "主鍵")]),
    ]
    result = check_metadata_completeness(tables)
    assert result["summary"]["tables_total"] == 1
    assert result["missing"] == []


def test_full_coverage_reports_100_percent():
    tables = [TableSpec(table_name="orders", description="訂單主檔", columns=[_col("id", "主鍵")])]
    result = check_metadata_completeness(tables)
    assert result["summary"]["coverage_pct"] == 100.0
    assert result["missing"] == []


def test_draft_comment_ddl_passes_allowlist_and_escapes_quotes():
    ddl = draft_comment_ddl("demo", "orders", {
        "table_comment": "訂單主檔，含使用者的 'VIP' 等級",
        "columns": {"status": "狀態（pending/paid）"},
    })
    assert "COMMENT ON TABLE" in ddl
    assert "COMMENT ON COLUMN" in ddl
    assert "''VIP''" in ddl
    assert check_ddl_allowlist(ddl) is None


def test_draft_comment_ddl_skips_empty_column_comments():
    ddl = draft_comment_ddl("demo", "orders", {
        "table_comment": "",
        "columns": {"status": "", "id": "主鍵"},
    })
    assert "COMMENT ON TABLE" not in ddl
    assert 'COMMENT ON COLUMN "orders"."id"' in ddl
    assert 'COMMENT ON COLUMN "orders"."status"' not in ddl
