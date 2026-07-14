"""app/api/routers/agent.py 的 HTTP 層測試：`POST /api/v1/agent/chat`。

路由層不接受外部傳入 `LLMProvider`（讀環境變數 `LLM_BASE_URL` 等），
respx 攔截同一個 BASE_URL（`_configure_settings` autouse fixture 已設定好）。
"""

import respx

from tests.agent.conftest import BASE_URL, chat_response, seed_business_db, tool_call


async def test_chat_json_mode_returns_turn_done_payload(client, seed_db):
    await seed_business_db(seed_db, "shop", "sqlite://")
    await seed_db.commit()

    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(return_value=chat_response(content="哈囉，有什麼需要幫忙？"))
        resp = await client.post("/api/v1/agent/chat", json={"message": "你好"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "哈囉，有什麼需要幫忙？"
    assert body["steps"] == []
    assert body["proposal"] is None
    assert body["design_request"] is None


async def test_chat_sse_mode_streams_tool_events(client, seed_db):
    await seed_business_db(seed_db, "shop", "sqlite://")
    await seed_db.commit()

    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").side_effect = [
            chat_response(tool_calls=[tool_call("c1", "list_databases", {})]),
            chat_response(content="共有 1 個資料庫。"),
        ]
        resp = await client.post(
            "/api/v1/agent/chat",
            json={"message": "有哪些資料庫？"},
            headers={"Accept": "text/event-stream"},
        )

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    text = resp.text
    assert "event: tool_call" in text
    assert "event: tool_result" in text
    assert "event: delta" in text
    assert "event: turn_done" in text
    assert "共有 1 個資料庫。" in text


async def test_chat_with_explicit_db_name(client, seed_db):
    await seed_business_db(seed_db, "shop", "sqlite://")
    await seed_db.commit()

    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(return_value=chat_response(content="收到"))
        resp = await client.post(
            "/api/v1/agent/chat", json={"message": "你好", "db_name": "shop"}
        )

    assert resp.status_code == 200
    assert resp.json()["reply"] == "收到"
