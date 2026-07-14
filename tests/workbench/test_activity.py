"""GET /activity：activity repo 查詢，倒序 + limit；關鍵操作（ddl-import 等）會寫入紀錄。"""


async def test_activity_records_ddl_import_and_orders_newest_first(client):
    await client.post("/api/v1/ddl-import", json={"title": "A", "ddl": "CREATE TABLE a (id int);"})
    await client.post("/api/v1/ddl-import", json={"title": "B", "ddl": "CREATE TABLE b (id int);"})

    resp = await client.get("/api/v1/activity")
    assert resp.status_code == 200
    events = resp.json()
    ddl_events = [e for e in events if e["event"] == "ddl_imported"]
    assert len(ddl_events) == 2
    # 倒序：最新（第二次匯入）在前
    assert ddl_events[0]["detail"]["table_count"] == 1
    timestamps = [e["created_at"] for e in events]
    assert timestamps == sorted(timestamps, reverse=True)


async def test_activity_respects_limit(client):
    for i in range(3):
        await client.post(
            "/api/v1/ddl-import", json={"title": str(i), "ddl": f"CREATE TABLE t{i} (id int);"}
        )

    resp = await client.get("/api/v1/activity", params={"limit": 2})
    assert resp.status_code == 200
    assert len(resp.json()) == 2
