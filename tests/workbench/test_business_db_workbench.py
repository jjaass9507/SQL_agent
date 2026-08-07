"""POST /workbench/query|explain|nl2sql、GET /workbench/schema-tree：
以業務資料庫名稱為範圍的工作台（DB Agent 頁用，沒有 session）。"""

import pytest

from app.repos import settings as settings_repo
from app.repos.crypto import encrypt_db_url
from app.services.change_service import BUSINESS_DATABASES_KEY


@pytest.fixture
def register_business_db(db_session):
    """在 app_settings 登錄一個或多個業務資料庫連線（等同設定頁新增的結果）。"""

    async def _register(*entries: tuple[str, str]):
        await settings_repo.set_setting(
            db_session,
            BUSINESS_DATABASES_KEY,
            [
                {"name": name, "db_url_encrypted": encrypt_db_url(url)}
                for name, url in entries
            ],
        )
        await db_session.commit()

    return _register


async def test_query_without_any_business_db_configured(client):
    resp = await client.post("/api/v1/workbench/query", json={"sql": "SELECT 1"})
    assert resp.status_code == 400
    assert "業務資料庫" in resp.json()["detail"]


async def test_query_unknown_db_name(client, register_business_db):
    await register_business_db(("shop", "sqlite://"))
    resp = await client.post(
        "/api/v1/workbench/query", json={"sql": "SELECT 1", "db_name": "nope"}
    )
    assert resp.status_code == 400
    assert "nope" in resp.json()["detail"]


async def test_query_executes_against_named_db(client, register_business_db):
    await register_business_db(("shop", "sqlite://"))
    resp = await client.post(
        "/api/v1/workbench/query", json={"sql": "SELECT 1 AS a, 'x' AS b", "db_name": "shop"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["columns"] == ["a", "b"]
    assert body["rows"] == [[1, "x"]]
    assert body["truncated"] is False


async def test_query_defaults_to_first_db_when_name_omitted(client, register_business_db):
    await register_business_db(("first", "sqlite://"), ("second", "sqlite://"))
    resp = await client.post("/api/v1/workbench/query", json={"sql": "SELECT 1 AS a"})
    assert resp.status_code == 200
    assert resp.json()["rows"] == [[1]]


async def test_query_rejects_writes(client, register_business_db):
    """護欄與 session 範圍的端點共用，不是各自實作一份。"""
    await register_business_db(("shop", "sqlite://"))
    for sql in [
        "DELETE FROM t",
        "SELECT 1; SELECT 2",
        "EXPLAIN ANALYZE DELETE FROM t",
        "SELECT pg_terminate_backend(1)",
    ]:
        resp = await client.post(
            "/api/v1/workbench/query", json={"sql": sql, "db_name": "shop"}
        )
        assert resp.status_code == 400, f"should reject: {sql}"


async def test_explain_executes_against_named_db(client, register_business_db):
    await register_business_db(("shop", "sqlite://"))
    resp = await client.post(
        "/api/v1/workbench/explain", json={"sql": "SELECT 1", "db_name": "shop"}
    )
    assert resp.status_code == 200
    assert resp.json()["rows"]


async def test_schema_tree_without_business_db(client):
    resp = await client.get("/api/v1/workbench/schema-tree")
    assert resp.status_code == 400


async def test_schema_tree_surfaces_introspection_failure(client, register_business_db):
    """結構內省只支援 PostgreSQL；連不上或不支援時要回可讀的錯誤，不是空樹。

    session 範圍的版本在內省失敗時會退回「設計中的版本快照」，業務資料庫沒有
    這個後備來源，因此改為明確報錯——回空樹會讓使用者以為資料庫真的沒有表。
    """
    await register_business_db(("shop", "sqlite://"))
    resp = await client.get("/api/v1/workbench/schema-tree?db_name=shop")
    assert resp.status_code == 400
    assert resp.json()["detail"]


async def test_schema_tree_error_does_not_leak_credentials(client, register_business_db):
    await register_business_db(("shop", "postgresql://user:s3cret@db.internal/shop"))
    resp = await client.get("/api/v1/workbench/schema-tree?db_name=shop")
    assert resp.status_code == 400
    assert "s3cret" not in resp.text


async def test_query_is_logged_with_db_name(client, register_business_db, db_session):
    """稽核紀錄要留下查了哪個資料庫（session 範圍的版本記的是 session_id）。"""
    from app.repos import activity as activity_repo

    await register_business_db(("shop", "sqlite://"))
    await client.post("/api/v1/workbench/query", json={"sql": "SELECT 1", "db_name": "shop"})

    entries = await activity_repo.list_activity(db_session)
    logged = [e for e in entries if e.event == "query_executed"]
    assert logged, "查詢應寫入 activity_log"
    assert logged[0].detail_json["db"] == "shop"
