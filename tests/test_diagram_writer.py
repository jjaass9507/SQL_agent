"""Tests for the deterministic Mermaid ER generator."""
from agents.writers.diagram_writer import DiagramWriter, build_mermaid_er
from models.schema import ColumnSpec, TableSpec


def _schema():
    users = TableSpec("users", "使用者", [
        ColumnSpec("id", "uuid", False, "PK", is_primary_key=True),
        ColumnSpec("email", "varchar(255)", False, "信箱", is_unique=True, length=255),
    ])
    orders = TableSpec("orders", "訂單", [
        ColumnSpec("id", "uuid", False, "PK", is_primary_key=True),
        ColumnSpec("user_id", "uuid", False, "FK", is_foreign_key=True, references="users.id"),
        ColumnSpec("total", "numeric(10,2)", True, "金額"),
    ])
    return [users, orders]


def test_er_has_entities_and_keys():
    er = build_mermaid_er(_schema())
    assert er.startswith("erDiagram")
    assert "users {" in er
    assert "orders {" in er
    assert "uuid id PK" in er
    assert "varchar email UK" in er          # varchar(255) → varchar, unique → UK
    assert "uuid user_id FK" in er


def test_er_type_has_no_parentheses():
    er = build_mermaid_er(_schema())
    # No attribute type may carry length/precision parentheses (breaks Mermaid)
    for line in er.splitlines():
        assert "(" not in line and ")" not in line


def test_er_relationship_from_fk():
    er = build_mermaid_er(_schema())
    assert 'users ||--o{ orders : "user_id"' in er


def test_generate_emits_fenced_diagram_and_strips_llm_code(monkeypatch):
    # LLM returns prose plus a (broken) mermaid block — the block must be dropped
    monkeypatch.setattr(
        "agents.writers.diagram_writer.get_api",
        lambda: type("A", (), {"chat": lambda s, system_prompt, human_prompt:
                               "兩表以外鍵關聯。\n```mermaid\nerDiagram BROKEN(\n```"})(),
    )
    out = DiagramWriter().generate(_schema())
    assert "兩表以外鍵關聯。" in out          # prose kept
    assert "BROKEN(" not in out               # LLM's broken block stripped
    assert out.count("```mermaid") == 1       # exactly our deterministic block
    assert "erDiagram" in out
