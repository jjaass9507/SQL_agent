"""Tests for DDL dry-run validation (mocked psycopg2 — no real DB)."""
import sys
import types
from unittest.mock import patch

import pytest

from web.ddl_validator import validate_ddl


class _FakeCursor:
    def __init__(self, conn): self.conn = conn
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, *a):
        self.conn.executed.append(sql)
        if self.conn.fail_on and self.conn.fail_on in sql:
            raise Exception("syntax error at or near ...")


class _FakeConn:
    def __init__(self, fail_on=None):
        self.fail_on = fail_on
        self.executed, self.rolled_back, self.committed, self.closed = [], False, False, False
        self.autocommit = True
    def cursor(self): return _FakeCursor(self)
    def rollback(self): self.rolled_back = True
    def commit(self): self.committed = True
    def close(self): self.closed = True


def _install_psycopg2(monkeypatch, conn):
    mod = types.ModuleType("psycopg2")
    mod.connect = lambda *a, **k: conn
    monkeypatch.setitem(sys.modules, "psycopg2", mod)


def test_validate_ok(monkeypatch):
    conn = _FakeConn()
    _install_psycopg2(monkeypatch, conn)
    result = validate_ddl("CREATE TABLE t (id uuid primary key);", "postgresql://x/y")
    assert result == {"ok": True}
    assert conn.rolled_back is True        # always rolled back, never committed
    assert conn.committed is False
    assert conn.closed is True


def test_validate_reports_error(monkeypatch):
    conn = _FakeConn(fail_on="CREATE TABLE bad")
    _install_psycopg2(monkeypatch, conn)
    result = validate_ddl("CREATE TABLE bad (;", "postgresql://x/y")
    assert result["ok"] is False
    assert "syntax error" in result["error"]
    assert conn.rolled_back is True


def test_validate_empty_ddl():
    assert validate_ddl("  ", "postgresql://x/y")["ok"] is False


def test_validate_no_conn():
    assert validate_ddl("CREATE TABLE t(id int);", "")["ok"] is False


# ── endpoint ─────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_data(tmp_path, monkeypatch):
    import web.session_store as ss
    monkeypatch.setattr(ss, "DATA_DIR", tmp_path)


def test_endpoint_no_ddl_yet(monkeypatch):
    import app as application
    from web.session_store import create_session
    sid = create_session("t", mode="design")["id"]
    with application.app.test_client() as c:
        resp = c.post(f"/api/sessions/{sid}/validate-ddl")
    assert resp.status_code == 400


def test_endpoint_no_connection(monkeypatch):
    import app as application
    from web.session_store import create_session, update_session
    sid = create_session("t", mode="design")["id"]
    update_session(sid, {"outputs": {"03_ddl.sql": "CREATE TABLE t(id int);"}})
    monkeypatch.setattr("web.app_settings.get_database_url", lambda: "")
    with application.app.test_client() as c:
        resp = c.post(f"/api/sessions/{sid}/validate-ddl")
    assert resp.status_code == 400


def test_endpoint_success(monkeypatch):
    import app as application
    from web.session_store import create_session, update_session
    sid = create_session("t", mode="design")["id"]
    update_session(sid, {"outputs": {"03_ddl.sql": "CREATE TABLE t(id int);"},
                         "db_url": "postgresql://x/y"})
    with patch("web.ddl_validator.validate_ddl", return_value={"ok": True}):
        with application.app.test_client() as c:
            resp = c.post(f"/api/sessions/{sid}/validate-ddl")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
