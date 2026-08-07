"""POST /sessions/{id}/validate-ddl-text：確認頁編輯器裡尚未存檔的 DDL。

與 validate-ddl 的差別：那個驗的是文件產出後的 03_ddl.sql、且一定要有資料庫連線；
確認頁的編輯器發生在文件產出之前，設計 session 也常常沒接資料庫。
"""

import uuid


async def test_session_not_found(client):
    resp = await client.post(
        f"/api/v1/sessions/{uuid.uuid4()}/validate-ddl-text",
        json={"ddl": "CREATE TABLE t (id int);"},
    )
    assert resp.status_code == 404


async def test_parse_only_when_session_has_no_database(client, make_session):
    """沒有連線時仍要能驗——這正是確認頁最常見的狀態，不能直接拒絕。"""
    record = await make_session()
    resp = await client.post(
        f"/api/v1/sessions/{record.id}/validate-ddl-text",
        json={"ddl": "CREATE TABLE users (id uuid PRIMARY KEY, email varchar(255));"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["checked"] == "parse"
    assert body["table_count"] == 1


async def test_rejects_text_that_is_not_ddl(client, make_session):
    record = await make_session()
    resp = await client.post(
        f"/api/v1/sessions/{record.id}/validate-ddl-text",
        json={"ddl": "這不是建表語法，只是一段說明文字"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["checked"] == "parse"
    assert "CREATE TABLE" in body["error"]


async def test_counts_multiple_tables(client, make_session):
    record = await make_session()
    ddl = (
        "CREATE TABLE users (id uuid PRIMARY KEY);\n"
        "CREATE TABLE orders (id uuid PRIMARY KEY, user_id uuid);"
    )
    resp = await client.post(
        f"/api/v1/sessions/{record.id}/validate-ddl-text", json={"ddl": ddl}
    )
    assert resp.json()["table_count"] == 2


async def test_empty_ddl_is_rejected_by_schema(client, make_session):
    record = await make_session()
    resp = await client.post(
        f"/api/v1/sessions/{record.id}/validate-ddl-text", json={"ddl": ""}
    )
    assert resp.status_code == 422
