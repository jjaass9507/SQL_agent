"""Workbench must hide the platform's own tables when the session's target DB
is the same database as the platform storage DB."""
from unittest.mock import patch

import pytest

from web.db_schema import platform_table_names


@pytest.fixture(autouse=True)
def _isolate_data(tmp_path, monkeypatch):
    import web.session_store as ss
    monkeypatch.setattr(ss, "DATA_DIR", tmp_path)


def test_platform_table_names_covers_bookkeeping():
    names = platform_table_names()
    assert {"sessions", "messages", "activity_log", "alembic_version"} <= names


_TREE = {"tables": [
    {"name": "sessions", "columns": []},
    {"name": "activity_log", "columns": []},
    {"name": "customers", "columns": []},   # the user's real table
]}


def _session_with_db(url="postgresql://app/storage"):
    from web.session_store import create_session, update_session
    sid = create_session("t", mode="design")["id"]
    update_session(sid, {"db_url": url})
    return sid


def test_schema_tree_hides_platform_tables_when_same_db():
    import app as application
    sid = _session_with_db("postgresql://app/storage")
    with patch("web.db_manager.schema_tree", return_value=dict(_TREE)), \
         patch("web.app_settings.get_database_url", return_value="postgresql://app/storage"):
        with application.app.test_client() as c:
            data = c.get(f"/api/sessions/{sid}/schema-tree").get_json()
    names = {t["name"] for t in data["tables"]}
    assert names == {"customers"}              # platform tables hidden


def test_schema_tree_keeps_tables_when_different_db():
    import app as application
    sid = _session_with_db("postgresql://target/userdb")
    with patch("web.db_manager.schema_tree", return_value=dict(_TREE)), \
         patch("web.app_settings.get_database_url", return_value="postgresql://app/storage"):
        with application.app.test_client() as c:
            data = c.get(f"/api/sessions/{sid}/schema-tree").get_json()
    names = {t["name"] for t in data["tables"]}
    assert names == {"sessions", "activity_log", "customers"}  # nothing hidden


def test_nl2sql_context_excludes_platform_tables():
    import app as application
    sid = _session_with_db("postgresql://app/storage")
    captured = {}

    def fake_generate(question, schema_text):
        captured["schema_text"] = schema_text
        return {"sql": "SELECT 1"}

    with patch("web.db_manager.schema_tree", return_value=dict(_TREE)), \
         patch("web.app_settings.get_database_url", return_value="postgresql://app/storage"), \
         patch("web.nl2sql.generate_sql", side_effect=fake_generate):
        with application.app.test_client() as c:
            c.post(f"/api/sessions/{sid}/nl2sql", json={"question": "x"})
    assert "customers" in captured["schema_text"]
    assert "sessions" not in captured["schema_text"]
    assert "activity_log" not in captured["schema_text"]
