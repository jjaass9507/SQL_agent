"""POST /sessions/{id}/query 與 /explain：
護欄擋 DML/stacked statements、無 db_url、session 不存在。"""

import uuid


async def test_query_session_not_found(client):
    resp = await client.post(f"/api/v1/sessions/{uuid.uuid4()}/query", json={"sql": "SELECT 1"})
    assert resp.status_code == 404


async def test_query_no_db_url_configured(client, make_session):
    record = await make_session()
    resp = await client.post(f"/api/v1/sessions/{record.id}/query", json={"sql": "SELECT 1"})
    assert resp.status_code == 400


async def test_query_rejects_dml(client, make_session):
    record = await make_session(db_url="sqlite://")
    resp = await client.post(
        f"/api/v1/sessions/{record.id}/query", json={"sql": "DELETE FROM t"}
    )
    assert resp.status_code == 400
    assert "SELECT" in resp.json()["detail"] or resp.json()["detail"]


async def test_query_rejects_stacked_statements(client, make_session):
    record = await make_session(db_url="sqlite://")
    resp = await client.post(
        f"/api/v1/sessions/{record.id}/query", json={"sql": "SELECT 1; SELECT 2"}
    )
    assert resp.status_code == 400


async def test_query_executes_select(client, make_session):
    record = await make_session(db_url="sqlite://")
    resp = await client.post(
        f"/api/v1/sessions/{record.id}/query", json={"sql": "SELECT 1 AS a, 'x' AS b"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["columns"] == ["a", "b"]
    assert body["rows"] == [[1, "x"]]
    assert body["truncated"] is False


async def test_explain_session_not_found(client):
    resp = await client.post(f"/api/v1/sessions/{uuid.uuid4()}/explain", json={"sql": "SELECT 1"})
    assert resp.status_code == 404


async def test_explain_executes(client, make_session):
    record = await make_session(db_url="sqlite://")
    resp = await client.post(
        f"/api/v1/sessions/{record.id}/explain", json={"sql": "SELECT 1"}
    )
    assert resp.status_code == 200
    assert resp.json()["rows"]
