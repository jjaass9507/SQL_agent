"""Tests for DDL dry-run validation (mocked psycopg2 — no real DB).

The v0.5 source file (tests/test_ddl_validator.py) also had endpoint tests
against the Flask app.py / web.session_store — those are out of scope here
since v2's rules module has no HTTP layer; only the pure validate_ddl()
unit tests are ported.
"""
import sys
import types

from app.rules.ddl_validator import validate_ddl


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
