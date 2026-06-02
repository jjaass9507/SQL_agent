"""Tests for natural-language → SQL generation (workbench)."""
from unittest.mock import patch

import pytest

from web.nl2sql import format_schema, generate_sql


def _fake_api(response):
    return type("A", (), {"chat": lambda self, system_prompt, human_prompt: response})()


def test_format_schema_compact():
    tables = [
        {"name": "users", "columns": [
            {"name": "id", "type": "uuid", "is_pk": True, "is_fk": False},
            {"name": "email", "type": "varchar", "is_pk": False, "is_fk": False},
        ]},
        {"name": "orders", "columns": [
            {"name": "user_id", "type": "uuid", "is_pk": False, "is_fk": True, "fk_table": "users"},
        ]},
    ]
    out = format_schema(tables)
    assert "users(id uuid [PK], email varchar)" in out
    assert "orders(user_id uuid [FK->users])" in out


def test_generate_sql_returns_select(monkeypatch):
    monkeypatch.setattr("web.nl2sql.get_api",
                        lambda: _fake_api("```sql\nSELECT * FROM users LIMIT 100\n```"))
    result = generate_sql("列出使用者", "users(id uuid)")
    assert result == {"sql": "SELECT * FROM users LIMIT 100"}


def test_generate_sql_strips_plain_text(monkeypatch):
    monkeypatch.setattr("web.nl2sql.get_api",
                        lambda: _fake_api("SELECT count(*) FROM orders;"))
    assert generate_sql("幾筆訂單", "orders(id uuid)")["sql"] == "SELECT count(*) FROM orders"


def test_generate_sql_rejects_non_select(monkeypatch):
    monkeypatch.setattr("web.nl2sql.get_api",
                        lambda: _fake_api("DELETE FROM users"))
    result = generate_sql("刪掉使用者", "users(id uuid)")
    assert "error" in result and "sql" not in result


def test_generate_sql_empty_question():
    assert "error" in generate_sql("   ", "users(id uuid)")


# ── endpoint ─────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_data(tmp_path, monkeypatch):
    import web.session_store as ss
    monkeypatch.setattr(ss, "DATA_DIR", tmp_path)


def test_nl2sql_endpoint_requires_db(monkeypatch):
    import app as application
    from web.session_store import create_session
    sid = create_session("t", mode="design")["id"]  # no db_url
    with application.app.test_client() as c:
        resp = c.post(f"/api/sessions/{sid}/nl2sql", json={"question": "x"})
    assert resp.status_code == 400


def test_nl2sql_endpoint_returns_sql(monkeypatch):
    import app as application
    from web.session_store import create_session, update_session
    sid = create_session("t", mode="design")["id"]
    update_session(sid, {"db_url": "postgresql://x/y"})

    with patch("web.db_manager.schema_tree", return_value={"tables": [{"name": "users", "columns": []}]}), \
         patch("web.nl2sql.generate_sql", return_value={"sql": "SELECT * FROM users LIMIT 100"}):
        with application.app.test_client() as c:
            resp = c.post(f"/api/sessions/{sid}/nl2sql", json={"question": "列出使用者"})
    assert resp.status_code == 200
    assert resp.get_json()["sql"] == "SELECT * FROM users LIMIT 100"
