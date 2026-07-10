"""Tests for web/convention_checker.py — pure rule-based, no LLM/DB."""
from models.schema import ColumnSpec, TableSpec
from web.convention_checker import check_conventions, infer_conventions


def _col(name, data_type="text", is_pk=False, is_fk=False):
    return ColumnSpec(name=name, data_type=data_type, nullable=True, description="",
                      is_primary_key=is_pk, is_foreign_key=is_fk)


def _existing_tables():
    """3 snake_case tables, each with an `id` PK (integer) and created_at."""
    return [
        TableSpec(table_name="users", description="", columns=[
            _col("id", "integer", is_pk=True),
            _col("email", "varchar"),
            _col("created_at", "timestamptz"),
        ]),
        TableSpec(table_name="orders", description="", columns=[
            _col("id", "integer", is_pk=True),
            _col("user_id", "integer", is_fk=True),
            _col("created_at", "timestamptz"),
        ]),
        TableSpec(table_name="order_items", description="", columns=[
            _col("id", "integer", is_pk=True),
            _col("order_id", "integer", is_fk=True),
            _col("created_at", "timestamptz"),
        ]),
    ]


def test_sample_too_small_returns_empty_conventions():
    assert infer_conventions(_existing_tables()[:2]) == {}


def test_infer_conventions_detects_snake_case_majority():
    conventions = infer_conventions(_existing_tables())
    assert conventions["naming_style"] == "snake"
    assert conventions["pk_name"] == "id"
    assert conventions["timestamp_ratio"] == 1.0


def test_camel_case_design_table_triggers_naming_warning():
    conventions = infer_conventions(_existing_tables())
    design = [TableSpec(table_name="userProfile", description="", columns=[
        _col("id", "integer", is_pk=True),
        _col("createdAt", "timestamptz"),
    ])]
    warnings = check_conventions(design, conventions)
    assert any(w["code"] == "convention_naming" for w in warnings)


def test_missing_created_at_triggers_timestamps_warning():
    conventions = infer_conventions(_existing_tables())
    design = [TableSpec(table_name="products", description="", columns=[
        _col("id", "integer", is_pk=True),
        _col("name", "varchar"),
    ])]
    warnings = check_conventions(design, conventions)
    assert any(w["code"] == "convention_timestamps" for w in warnings)


def test_no_warnings_when_sample_too_small():
    small_sample = _existing_tables()[:2]
    warnings = check_conventions(_existing_tables(), infer_conventions(small_sample))
    assert warnings == []


def test_warning_shape_matches_schema_advisor():
    conventions = infer_conventions(_existing_tables())
    design = [TableSpec(table_name="userProfile", description="", columns=[
        _col("id", "integer", is_pk=True),
    ])]
    warnings = check_conventions(design, conventions)
    assert warnings
    for w in warnings:
        assert set(w.keys()) >= {"table", "level", "code", "message"}
        assert w["level"] in ("warn", "info")
