"""GET /settings、POST|DELETE /settings/business-db：連線字串遮罩斷言、增刪流程、管理員保護。"""

from app.config import get_settings

# 業務資料庫連線的增刪需要管理員權杖；權杖值由 conftest 的 _settings_env 設定。
_ADMIN = {"X-Admin-Token": "test-admin-token"}


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
        "/api/v1/settings/business-db",
        json={"name": "biz", "url": "sqlite:///x.db"},
        headers=_ADMIN,
    )
    assert resp.status_code == 400


async def test_add_business_db_reports_connection_failure_sanitized(client, monkeypatch):
    monkeypatch.setattr("app.services.dbops.execute_query", _fake_execute_query_fail)
    resp = await client.post(
        "/api/v1/settings/business-db",
        json={"name": "biz", "url": "postgresql://user:secret@host/db"},
        headers=_ADMIN,
    )
    assert resp.status_code == 400
    assert "secret" not in resp.text


async def test_add_business_db_success_never_leaks_password(client, monkeypatch):
    monkeypatch.setattr("app.services.dbops.execute_query", _fake_execute_query_ok)
    resp = await client.post(
        "/api/v1/settings/business-db",
        json={"name": "biz", "url": "postgresql://user:hunter2@host/db"},
        headers=_ADMIN,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "hunter2" not in resp.text
    dbs = body["business_databases"]
    assert len(dbs) == 1
    assert dbs[0] == {"name": "biz", "masked_url": "postgresql://user:***@host/db"}


async def test_add_business_db_replaces_same_name(client, monkeypatch):
    monkeypatch.setattr("app.services.dbops.execute_query", _fake_execute_query_ok)
    await client.post(
        "/api/v1/settings/business-db",
        json={"name": "biz", "url": "postgresql://user:pass1@host/db"},
        headers=_ADMIN,
    )
    resp = await client.post(
        "/api/v1/settings/business-db",
        json={"name": "biz", "url": "postgresql://user:pass2@host2/db2"},
        headers=_ADMIN,
    )
    body = resp.json()
    assert len(body["business_databases"]) == 1
    assert body["business_databases"][0]["masked_url"] == "postgresql://user:***@host2/db2"


async def test_remove_business_db(client, monkeypatch):
    monkeypatch.setattr("app.services.dbops.execute_query", _fake_execute_query_ok)
    await client.post(
        "/api/v1/settings/business-db",
        json={"name": "biz", "url": "postgresql://user:pass@host/db"},
        headers=_ADMIN,
    )
    resp = await client.delete(
        "/api/v1/settings/business-db", params={"name": "biz"}, headers=_ADMIN
    )
    assert resp.status_code == 200
    assert resp.json()["business_databases"] == []


# ── 管理員保護（A1a）：業務資料庫連線＝平台指向哪個正式庫，不可匿名增刪 ──────────


async def test_add_business_db_without_admin_token_is_rejected(client):
    resp = await client.post(
        "/api/v1/settings/business-db",
        json={"name": "biz", "url": "postgresql://user:pass@host/db"},
    )
    assert resp.status_code == 401


async def test_remove_business_db_without_admin_token_is_rejected(client):
    resp = await client.delete("/api/v1/settings/business-db", params={"name": "biz"})
    assert resp.status_code == 401


async def test_business_db_endpoints_fail_closed_when_admin_token_unset(client, monkeypatch):
    """未設定 ADMIN_TOKEN 時一律 403（fail-closed），不是放行。"""
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    get_settings.cache_clear()

    resp = await client.post(
        "/api/v1/settings/business-db",
        json={"name": "biz", "url": "postgresql://user:pass@host/db"},
        headers=_ADMIN,
    )
    assert resp.status_code == 403


async def test_get_settings_stays_readable_without_admin_token(client):
    """讀取（連線字串已遮罩）不受管理員保護影響，維持原行為。"""
    resp = await client.get("/api/v1/settings")
    assert resp.status_code == 200
