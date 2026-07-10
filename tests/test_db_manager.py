"""Unit tests for web/db_manager.py — mocked psycopg2, no real DB required."""
from unittest.mock import MagicMock, patch

import pytest

from web.db_manager import (
    _check_sql,
    execute_query,
    explain_query,
    get_table_ddl,
    list_tables,
)


# ── _check_sql ────────────────────────────────────────────────────────────────

def test_check_sql_allows_select():
    assert _check_sql("SELECT 1") is None
    assert _check_sql("  select * from users  ") is None
    assert _check_sql("SELECT id, name FROM orders WHERE status = 'paid'") is None


def test_check_sql_allows_explain_prefix():
    # Note: explain_query wraps with EXPLAIN itself; _check_sql checks the inner SQL
    assert _check_sql("SELECT count(*) FROM sessions") is None


def test_check_sql_rejects_empty():
    assert _check_sql("") is not None
    assert _check_sql("   ") is not None
    assert _check_sql(None) is not None  # type: ignore


def test_check_sql_rejects_ddl():
    for stmt in [
        "CREATE TABLE t (id int)",
        "DROP TABLE users",
        "ALTER TABLE t ADD COLUMN x int",
        "TRUNCATE sessions",
    ]:
        assert _check_sql(stmt) is not None, f"Should reject: {stmt}"


def test_check_sql_rejects_dcl():
    assert _check_sql("GRANT SELECT ON t TO u") is not None
    assert _check_sql("REVOKE ALL ON t FROM u") is not None


def test_check_sql_rejects_dml():
    assert _check_sql("INSERT INTO t VALUES (1)") is not None
    assert _check_sql("UPDATE t SET x = 1") is not None
    assert _check_sql("DELETE FROM t WHERE id = 1") is not None
    assert _check_sql("MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET x=1") is not None


def test_check_sql_rejects_leading_comment_bypass():
    # The forbidden verb is hidden behind a leading comment.
    assert _check_sql("/* x */ DROP TABLE users") is not None
    assert _check_sql("-- comment\nDROP TABLE users") is not None
    assert _check_sql("  /* a */ /* b */  TRUNCATE sessions") is not None


def test_check_sql_rejects_cte_dml():
    assert _check_sql("WITH c AS (SELECT 1) DELETE FROM users") is not None
    assert _check_sql("WITH c AS (SELECT 1) UPDATE users SET x = 1") is not None


def test_check_sql_rejects_select_into():
    assert _check_sql("SELECT * INTO new_table FROM old_table") is not None


def test_check_sql_allows_select_with_keywords_in_literals():
    # Keywords appearing only inside string literals / identifiers must not trip.
    assert _check_sql("SELECT * FROM notes WHERE body = 'please delete this'") is None
    assert _check_sql("SELECT create_date, updated_at FROM events") is None
    assert _check_sql("SELECT * FROM updates WHERE id IN (1, 2)") is None


def test_check_sql_rejects_stacked_statements():
    # A trailing statement must not slip through keyword checks that only
    # look at the first statement.
    assert _check_sql("SELECT 1; DELETE FROM t") is not None
    assert _check_sql("SELECT 1; SELECT 2") is not None


def test_check_sql_rejects_string_semicolon_bypass():
    # A `;` inside a string literal must not be mistaken for a statement
    # boundary that "hides" the second, forbidden statement.
    assert _check_sql("SELECT ';' ; DROP TABLE x") is not None


# ── execute_query ─────────────────────────────────────────────────────────────

def test_execute_query_rejects_forbidden_sql():
    result = execute_query("postgresql://x/y", "DROP TABLE users")
    assert "error" in result


def test_execute_query_connection_error():
    with patch("psycopg2.connect", side_effect=Exception("connection refused")):
        result = execute_query("postgresql://bad/db", "SELECT 1")
    assert "error" in result
    assert "連線失敗" in result["error"]


def _make_mock_conn(rows, col_names):
    """Helper: build a mock psycopg2 connection returning given rows."""
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = lambda s: s
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchmany.return_value = [{col: val for col, val in zip(col_names, row)} for row in rows]
    mock_cursor.description = [(col,) for col in col_names]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    return mock_conn


def test_execute_query_returns_rows():
    mock_conn = _make_mock_conn([(1, "Alice"), (2, "Bob")], ["id", "name"])
    with patch("psycopg2.connect", return_value=mock_conn):
        result = execute_query("postgresql://x/y", "SELECT id, name FROM users")
    assert result["columns"] == ["id", "name"]
    assert result["rows"] == [[1, "Alice"], [2, "Bob"]]
    assert result["row_count"] == 2
    assert result["truncated"] is False


def test_execute_query_truncates_at_limit():
    # Return limit+1 rows → truncated=True, rows capped at limit
    rows = [(i,) for i in range(11)]  # 11 rows with limit=10
    mock_conn = _make_mock_conn(rows, ["id"])
    with patch("psycopg2.connect", return_value=mock_conn):
        result = execute_query("postgresql://x/y", "SELECT id FROM t", limit=10)
    assert result["truncated"] is True
    assert len(result["rows"]) == 10


def test_execute_query_empty_result():
    mock_conn = _make_mock_conn([], ["id", "name"])
    with patch("psycopg2.connect", return_value=mock_conn):
        result = execute_query("postgresql://x/y", "SELECT id, name FROM empty_table")
    assert result["columns"] == ["id", "name"]
    assert result["rows"] == []
    assert result["row_count"] == 0


# ── explain_query ─────────────────────────────────────────────────────────────

def test_explain_query_rejects_forbidden_sql():
    result = explain_query("postgresql://x/y", "INSERT INTO t VALUES (1)")
    assert "error" in result


def test_explain_query_returns_plan():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = lambda s: s
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchall.return_value = [("Seq Scan on users  (cost=0.00..1.01 rows=1 width=10)",)]
    mock_conn.cursor.return_value = mock_cursor
    with patch("psycopg2.connect", return_value=mock_conn):
        result = explain_query("postgresql://x/y", "SELECT * FROM users")
    assert "plan" in result
    assert "Seq Scan" in result["plan"]


def test_explain_query_connection_error():
    with patch("psycopg2.connect", side_effect=Exception("refused")):
        result = explain_query("postgresql://bad/db", "SELECT 1")
    assert "error" in result


# ── list_tables ───────────────────────────────────────────────────────────────

def test_list_tables_returns_names():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = lambda s: s
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchall.return_value = [("orders",), ("users",)]
    mock_conn.cursor.return_value = mock_cursor
    with patch("psycopg2.connect", return_value=mock_conn):
        result = list_tables("postgresql://x/y")
    assert result == {"tables": ["orders", "users"]}


def test_list_tables_connection_error():
    with patch("psycopg2.connect", side_effect=Exception("refused")):
        result = list_tables("postgresql://bad/db")
    assert "error" in result


# ── get_table_ddl ─────────────────────────────────────────────────────────────

def test_get_table_ddl_returns_ddl():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = lambda s: s
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchall.return_value = [
        ("id", "integer", None, "NO", "nextval('users_id_seq')"),
        ("email", "character varying", 255, "NO", None),
        ("name", "text", None, "YES", None),
    ]
    mock_conn.cursor.return_value = mock_cursor
    with patch("psycopg2.connect", return_value=mock_conn):
        result = get_table_ddl("postgresql://x/y", "users")
    assert "ddl" in result
    assert "CREATE TABLE" in result["ddl"]
    assert "email" in result["ddl"]


def test_get_table_ddl_not_found():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = lambda s: s
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchall.return_value = []
    mock_conn.cursor.return_value = mock_cursor
    with patch("psycopg2.connect", return_value=mock_conn):
        result = get_table_ddl("postgresql://x/y", "nonexistent")
    assert "error" in result
