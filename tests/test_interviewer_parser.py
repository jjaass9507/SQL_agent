"""Tests for Interviewer XML tag parsing — no API calls needed."""
import json
from agents.interviewer import _parse_tables


def _make_raw(overrides: dict = {}) -> list[dict]:
    col = {
        "name": "id",
        "data_type": "UUID",
        "length": None,
        "nullable": False,
        "default": "gen_random_uuid()",
        "description": "主鍵",
        "is_primary_key": True,
        "is_foreign_key": False,
        "references": None,
        "is_unique": False,
        "is_indexed": False,
    }
    table = {
        "table_name": "users",
        "description": "使用者表",
        "columns": [col],
        "constraints": [],
        "related_tables": [],
    }
    table.update(overrides)
    return [table]


def test_parse_tables_basic():
    raw = json.dumps(_make_raw())
    tables = _parse_tables(raw)
    assert tables is not None
    assert len(tables) == 1
    assert tables[0].table_name == "users"


def test_parse_tables_column_fields():
    raw = json.dumps(_make_raw())
    tables = _parse_tables(raw)
    col = tables[0].columns[0]
    assert col.name == "id"
    assert col.is_primary_key is True
    assert col.nullable is False
    assert col.default == "gen_random_uuid()"


def test_parse_tables_multiple_tables():
    second = {
        "table_name": "orders",
        "description": "訂單表",
        "columns": _make_raw()[0]["columns"],
        "constraints": ["CHECK (amount > 0)"],
        "related_tables": ["users"],
    }
    raw = json.dumps(_make_raw() + [second])
    tables = _parse_tables(raw)
    assert len(tables) == 2
    assert tables[1].table_name == "orders"
    assert tables[1].constraints == ["CHECK (amount > 0)"]


def test_parse_tables_invalid_json():
    result = _parse_tables("not valid json {{{")
    assert result is None


def test_parse_tables_empty_list():
    result = _parse_tables("[]")
    assert result is None
