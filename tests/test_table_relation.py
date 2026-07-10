"""Tests for web/table_relation.py — deterministic scoring, no LLM/DB."""
from models.schema import ColumnSpec, TableSpec
from web.table_relation import find_related


def _col(name, data_type="text", is_pk=False, is_fk=False):
    return ColumnSpec(name=name, data_type=data_type, nullable=True, description="",
                      is_primary_key=is_pk, is_foreign_key=is_fk)


def _existing_tables():
    return [
        TableSpec(table_name="products", description="商品主檔", columns=[
            _col("id", "integer", is_pk=True),
            _col("name", "varchar"),
            _col("price", "numeric"),
        ]),
        TableSpec(table_name="orders", description="訂單主檔資料表", columns=[
            _col("id", "integer", is_pk=True),
            _col("user_id", "integer", is_fk=True),
            _col("status", "varchar"),
        ]),
    ]


def test_fk_suggestion_from_xxx_id_column():
    design = [TableSpec(table_name="order_items", description="", columns=[
        _col("id", "integer", is_pk=True),
        _col("product_id", "integer", is_fk=True),
    ])]
    result = find_related("", design, _existing_tables())
    assert {"from_table": "order_items", "column": "product_id", "to_table": "products"} \
        in result["fk_suggestions"]


def test_high_column_overlap_flags_duplicate_risk():
    design = [TableSpec(table_name="orders_v2", description="", columns=[
        _col("id", "integer", is_pk=True),
        _col("user_id", "integer", is_fk=True),
        _col("status", "varchar"),
    ])]
    result = find_related("", design, _existing_tables())
    assert any(d["existing_table"] == "orders" and d["design_table"] == "orders_v2"
              for d in result["duplicate_risks"])


def test_requirement_text_keyword_hits_existing_table():
    result = find_related("我想查詢訂單的歷史紀錄", None, _existing_tables())
    related_names = [r["table"] for r in result["related"]]
    assert "orders" in related_names


def test_no_requirement_no_design_returns_empty_related():
    result = find_related("", None, _existing_tables())
    assert result == {"related": [], "fk_suggestions": [], "duplicate_risks": []}


def test_low_column_overlap_does_not_flag_duplicate():
    design = [TableSpec(table_name="shipments", description="", columns=[
        _col("id", "integer", is_pk=True),
        _col("tracking_no", "varchar"),
    ])]
    result = find_related("", design, _existing_tables())
    assert result["duplicate_risks"] == []
