"""GET /settings、POST|DELETE /settings/business-db：連線字串遮罩斷言、增刪流程。"""

from app.config import get_settings


async def _fake_execute_query_ok(db_url, sql, max_rows=200):
    return {"columns": ["?column?"], "rows": [[1]], "truncated": False}


async def _fake_execute_query_fail(db_url, sql, max_rows=200):
    raise RuntimeError(f"connection failed dsn={db_url}")


async def test_get_settings_masks_and_reports_backend(client, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://svc:hunter2@platform-host/app")
    get_settings.cache_clear()

    resp = await client.get("/api/v1/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["backend"] == "postgresql"
    assert "hunter2" not in body["masked_url"]
    assert body["business_databases"] == []


async def test_add_business_db_rejects_non_postgres_scheme(client):
    resp = await client.post(
        "/api/v1/settings/business-db", json={"name": "biz", "url": "sqlite:///x.db"}
    )
    assert resp.status_code == 400


async def test_add_business_db_reports_connection_failure_sanitized(client, monkeypatch):
    monkeypatch.setattr("app.services.dbops.execute_query", _fake_execute_query_fail)
    resp = await client.post(
        "/api/v1/settings/business-db",
        json={"name": "biz", "url": "postgresql://user:secret@host/db"},
    )
    assert resp.status_code == 400
    assert "secret" not in resp.text


async def test_add_business_db_success_never_leaks_password(client, monkeypatch):
    monkeypatch.setattr("app.services.dbops.execute_query", _fake_execute_query_ok)
    resp = await client.post(
        "/api/v1/settings/business-db",
        json={"name": "biz", "url": "postgresql://user:hunter2@host/db"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "hunter2" not in resp.text
    dbs = body["business_databases"]
    assert len(dbs) == 1
    assert dbs[0] == {
        "name": "biz",
        "masked_url": "postgresql://user:***@host/db",
        "default_schema": None,
    }


async def test_add_business_db_stores_default_schema(client, monkeypatch):
    monkeypatch.setattr("app.services.dbops.execute_query", _fake_execute_query_ok)
    resp = await client.post(
        "/api/v1/settings/business-db",
        json={
            "name": "biz",
            "url": "postgresql://user:pass@host/db",
            "default_schema": "warehouse",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["business_databases"][0]["default_schema"] == "warehouse"


async def test_add_business_db_replaces_same_name(client, monkeypatch):
    monkeypatch.setattr("app.services.dbops.execute_query", _fake_execute_query_ok)
    await client.post(
        "/api/v1/settings/business-db",
        json={"name": "biz", "url": "postgresql://user:pass1@host/db"},
    )
    resp = await client.post(
        "/api/v1/settings/business-db",
        json={"name": "biz", "url": "postgresql://user:pass2@host2/db2"},
    )
    body = resp.json()
    assert len(body["business_databases"]) == 1
    assert body["business_databases"][0]["masked_url"] == "postgresql://user:***@host2/db2"


async def test_remove_business_db(client, monkeypatch):
    monkeypatch.setattr("app.services.dbops.execute_query", _fake_execute_query_ok)
    await client.post(
        "/api/v1/settings/business-db",
        json={"name": "biz", "url": "postgresql://user:pass@host/db"},
    )
    resp = await client.delete("/api/v1/settings/business-db", params={"name": "biz"})
    assert resp.status_code == 200
    assert resp.json()["business_databases"] == []
