import json
from pathlib import Path
from models.schema import ColumnSpec, TableSpec


def _load_fixture() -> list[TableSpec]:
    raw = json.loads((Path(__file__).parent / "fixtures" / "sample_spec.json").read_text())
    tables = []
    for t in raw:
        columns = [ColumnSpec(**c) for c in t["columns"]]
        tables.append(TableSpec(
            table_name=t["table_name"],
            description=t["description"],
            columns=columns,
            constraints=t.get("constraints", []),
            related_tables=t.get("related_tables", []),
        ))
    return tables


def test_load_fixture():
    tables = _load_fixture()
    assert len(tables) == 2
    assert tables[0].table_name == "orders"
    assert tables[1].table_name == "order_items"


def test_column_flags():
    tables = _load_fixture()
    orders = tables[0]
    pk_cols = [c for c in orders.columns if c.is_primary_key]
    assert len(pk_cols) == 1
    assert pk_cols[0].name == "id"


def test_constraints():
    tables = _load_fixture()
    items = tables[1]
    assert len(items.constraints) == 2
    assert "CHECK (quantity > 0)" in items.constraints
