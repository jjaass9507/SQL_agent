"""Tests for transactional DDL execution (mocked psycopg2 — no real DB)."""
import sys
import types

from app.rules.ddl_executor import execute_ddl


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
        self.executed = []
        self.rolled_back = False
        self.committed = False
        self.closed = False
        # Deliberately no `autocommit` attribute pre-set: execute_ddl must
        # never set conn.autocommit — the whole call runs in one transaction.
    def cursor(self): return _FakeCursor(self)
    def rollback(self): self.rolled_back = True
    def commit(self): self.committed = True
    def close(self): self.closed = True


def _install_psycopg2(monkeypatch, conn):
    mod = types.ModuleType("psycopg2")
    mod.connect = lambda *a, **k: conn
    monkeypatch.setitem(sys.modules, "psycopg2", mod)


def test_execute_ddl_success_commits(monkeypatch):
    conn = _FakeConn()
    _install_psycopg2(monkeypatch, conn)
    result = execute_ddl("postgresql://x/y", "CREATE TABLE a (id int); CREATE TABLE b (id int);")
    assert result == {"ok": True, "statements_run": 2}
    assert conn.committed is True
    assert conn.rolled_back is False
    assert conn.closed is True
    assert not hasattr(conn, "autocommit")


def test_execute_ddl_failure_rolls_back(monkeypatch):
    conn = _FakeConn(fail_on="CREATE TABLE bad")
    _install_psycopg2(monkeypatch, conn)
    result = execute_ddl(
        "postgresql://x/y",
        "CREATE TABLE ok (id int); CREATE TABLE bad (id int); CREATE TABLE never_run (id int);",
    )
    assert result["ok"] is False
    assert "syntax error" in result["error"]
    assert conn.rolled_back is True
    assert conn.committed is False
    assert conn.closed is True
    # execution stops at the failing statement — the third never runs
    assert len(conn.executed) == 2
    assert not hasattr(conn, "autocommit")


def test_execute_ddl_no_psycopg2(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg2", None)
    result = execute_ddl("postgresql://x/y", "CREATE TABLE a (id int);")
    assert result["ok"] is False
