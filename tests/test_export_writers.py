"""Tests for pure-template export writers (DBML / PlantUML / JSON Schema / CSV)."""
import json

from models.schema import ColumnSpec, TableSpec
from agents.writers.dbml_writer import DBMLWriter
from agents.writers.plantuml_writer import PlantUMLWriter
from agents.writers.json_schema_writer import JSONSchemaWriter
from agents.writers.data_dict_writer import DataDictWriter


def _schema():
    users = TableSpec("users", "使用者", [
        ColumnSpec("id", "uuid", False, "PK", is_primary_key=True),
        ColumnSpec("email", "varchar", False, "信箱", is_unique=True, length=255),
    ])
    orders = TableSpec("orders", "訂單", [
        ColumnSpec("id", "uuid", False, "PK", is_primary_key=True),
        ColumnSpec("user_id", "uuid", False, "下單者", is_foreign_key=True, references="users.id"),
        ColumnSpec("amount", "integer", True, "金額"),
    ])
    return [users, orders]


def test_dbml_tables_and_refs():
    out = DBMLWriter().generate(_schema())
    assert "Table users {" in out
    assert "email varchar(255) [unique, not null" in out
    assert "Ref: orders.user_id > users.id" in out


def test_plantuml_entities_and_relation():
    out = PlantUMLWriter().generate(_schema())
    assert out.startswith("@startuml")
    assert out.rstrip().endswith("@enduml")
    assert "entity users {" in out
    assert "users ||--o{ orders" in out


def test_json_schema_structure():
    out = JSONSchemaWriter().generate(_schema())
    doc = json.loads(out)
    assert "definitions" in doc
    users = doc["definitions"]["users"]
    assert users["properties"]["email"]["type"] == "string"
    assert users["properties"]["email"]["maxLength"] == 255
    assert "id" in users["required"]          # not nullable
    assert doc["definitions"]["orders"]["properties"]["amount"]["type"] == "integer"


def test_json_schema_amount_optional():
    out = JSONSchemaWriter().generate(_schema())
    orders = json.loads(out)["definitions"]["orders"]
    assert "amount" not in orders.get("required", [])  # nullable → optional


def test_data_dict_csv_header_and_rows():
    out = DataDictWriter().generate(_schema())
    lines = out.strip().splitlines()
    assert lines[0].startswith("table,column,data_type,length,nullable")
    # one row per column → 2 + 3 = 5 data rows
    assert len(lines) == 1 + 5
    assert any(row.startswith("users,email,varchar,255") for row in lines)
