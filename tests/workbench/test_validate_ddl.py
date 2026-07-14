"""POST /sessions/{id}/validate-ddl：
dry-run 交給 rules.ddl_validator（fake psycopg2，無真實 DB）。"""

import sys
import types
import uuid

from app.repos import outputs


class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, *a):
        self.conn.executed.append(sql)
        if self.conn.fail_on and self.conn.fail_on in sql:
            raise Exception(
                f"connection failed dsn=postgresql://user:secret@host/db: {self.conn.fail_on}"
            )


class _FakeConn:
    def __init__(self, fail_on=None):
        self.fail_on = fail_on
        self.executed = []
        self.rolled_back = False
        self.autocommit = True

    def cursor(self):
        return _FakeCursor(self)

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


def _install_fake_psycopg2(monkeypatch, conn):
    mod = types.ModuleType("psycopg2")
    mod.connect = lambda *a, **k: conn
    monkeypatch.setitem(sys.modules, "psycopg2", mod)


async def test_validate_ddl_session_not_found(client):
    resp = await client.post(f"/api/v1/sessions/{uuid.uuid4()}/validate-ddl")
    assert resp.status_code == 404


async def test_validate_ddl_no_output_yet(client, make_session):
    record = await make_session(db_url="postgresql://user:secret@host/db")
    resp = await client.post(f"/api/v1/sessions/{record.id}/validate-ddl")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "尚未產生 DDL" in body["error"]


async def test_validate_ddl_no_db_url(client, make_session, db_session):
    record = await make_session()
    await outputs.upsert_output(
        db_session, record.id, "03_ddl.sql", "CREATE TABLE t (id uuid primary key);"
    )
    await db_session.commit()

    resp = await client.post(f"/api/v1/sessions/{record.id}/validate-ddl")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "資料庫連線" in body["error"]


async def test_validate_ddl_ok(client, make_session, db_session, monkeypatch):
    record = await make_session(db_url="postgresql://user:secret@host/db")
    await outputs.upsert_output(
        db_session, record.id, "03_ddl.sql", "CREATE TABLE t (id uuid primary key);"
    )
    await db_session.commit()
    _install_fake_psycopg2(monkeypatch, _FakeConn())

    resp = await client.post(f"/api/v1/sessions/{record.id}/validate-ddl")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "error": None}


async def test_validate_ddl_error_is_sanitized(client, make_session, db_session, monkeypatch):
    record = await make_session(db_url="postgresql://user:secret@host/db")
    await outputs.upsert_output(
        db_session, record.id, "03_ddl.sql", "CREATE TABLE bad (;"
    )
    await db_session.commit()
    _install_fake_psycopg2(monkeypatch, _FakeConn(fail_on="CREATE TABLE bad"))

    resp = await client.post(f"/api/v1/sessions/{record.id}/validate-ddl")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "secret" not in body["error"]
