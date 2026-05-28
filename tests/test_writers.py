import json
from pathlib import Path
from models.schema import ColumnSpec, TableSpec
from agents.writers.spec_writer import SpecWriter


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


def test_spec_writer_contains_table_names():
    tables = _load_fixture()
    result = SpecWriter().generate(tables)
    assert "orders" in result
    assert "order_items" in result


def test_spec_writer_contains_all_columns():
    tables = _load_fixture()
    result = SpecWriter().generate(tables)
    for t in tables:
        for c in t.columns:
            assert c.name in result


def test_spec_writer_pk_marked():
    tables = _load_fixture()
    result = SpecWriter().generate(tables)
    assert "✓" in result  # PK column should have a checkmark


def test_spec_writer_constraints_shown():
    tables = _load_fixture()
    result = SpecWriter().generate(tables)
    assert "CHECK (quantity > 0)" in result
