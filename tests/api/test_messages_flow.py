"""POST /sessions/{id}/messages（JSON 模式）：tables_ready 轉換 phase、輸入驗證。"""

import respx

from tests.api.conftest import BASE_URL, interview_turn_payload, sample_table
from tests.llm.conftest import chat_completion_response


async def test_json_mode_turn_without_tables_keeps_collecting_phase(client):
    session = (await client.post("/api/v1/sessions", json={})).json()

    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(
                content=interview_turn_payload("請問這張表叫什麼名字？")
            )
        )
        resp = await client.post(
            f"/api/v1/sessions/{session['id']}/messages",
            json={"content": "我想要一個使用者資料表"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["tables_ready"] is False
    assert body["tables"] is None

    detail = (await client.get(f"/api/v1/sessions/{session['id']}")).json()
    assert detail["phase"] == "collecting"


async def test_json_mode_turn_with_tables_sets_confirming_and_creates_version(client):
    session = (await client.post("/api/v1/sessions", json={})).json()
    tables = [sample_table("users")]

    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(
                content=interview_turn_payload(
                    "這是設計結果", tables=tables, summary=["需要使用者表"]
                )
            )
        )
        resp = await client.post(
            f"/api/v1/sessions/{session['id']}/messages",
            json={"content": "我想要一個使用者資料表，有 id 欄位"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["tables_ready"] is True
    assert body["tables"][0]["table_name"] == "users"
    assert body["summary"] == ["需要使用者表"]

    detail = (await client.get(f"/api/v1/sessions/{session['id']}")).json()
    assert detail["phase"] == "confirming"
    assert detail["latest_version"] == 1
    assert detail["latest_tables"][0]["table_name"] == "users"
    assert detail["latest_key_points"] == ["需要使用者表"]


async def test_message_content_empty_returns_422(client):
    session = (await client.post("/api/v1/sessions", json={})).json()

    resp = await client.post(f"/api/v1/sessions/{session['id']}/messages", json={"content": ""})

    assert resp.status_code == 422


async def test_message_content_too_long_returns_422(client):
    session = (await client.post("/api/v1/sessions", json={})).json()

    resp = await client.post(
        f"/api/v1/sessions/{session['id']}/messages", json={"content": "x" * 10001}
    )

    assert resp.status_code == 422


async def test_send_message_session_not_found_returns_404(client):
    resp = await client.post(
        "/api/v1/sessions/00000000-0000-0000-0000-000000000000/messages",
        json={"content": "hi"},
    )

    assert resp.status_code == 404


async def test_list_messages_returns_history_for_restore(client):
    """重整後前端要能還原對話：沒有這個端點，使用者會看到空白畫面以為 AI 失憶。"""
    session = (await client.post("/api/v1/sessions", json={})).json()
    tables = [sample_table("users")]
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(
                content=interview_turn_payload("好的，我整理如下", tables=tables)
            )
        )
        await client.post(
            f"/api/v1/sessions/{session['id']}/messages", json={"content": "我要一張使用者表"}
        )

    resp = await client.get(f"/api/v1/sessions/{session['id']}/messages")
    assert resp.status_code == 200
    history = resp.json()
    assert [m["role"] for m in history] == ["user", "ai"]
    assert history[0]["content"] == "我要一張使用者表"
    assert history[1]["content"] == "好的，我整理如下"


async def test_list_messages_hides_agent_tool_records(client, db_engine):
    """DB Agent 的 tool_call/tool_result 也存在同一張 messages 表，
    但那不是給人看的文字，不該出現在對話歷史裡。"""
    import uuid

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.repos import messages as messages_repo
    from app.services import agent_service

    session = (await client.post("/api/v1/sessions", json={})).json()
    async with async_sessionmaker(db_engine, expire_on_commit=False)() as db:
        sid = uuid.UUID(session["id"])
        await messages_repo.add_message(db, sid, "user", "查一下 orders")
        await messages_repo.add_message(
            db, sid, "ai", agent_service._encode_tool_call("c1", "get_schema", {})
        )
        await messages_repo.add_message(
            db, sid, "ai", agent_service._encode_tool_result("c1", "get_schema", "{}")
        )
        await messages_repo.add_message(db, sid, "ai", "orders 有 5 個欄位")
        await db.commit()

    history = (await client.get(f"/api/v1/sessions/{session['id']}/messages")).json()
    assert [m["content"] for m in history] == ["查一下 orders", "orders 有 5 個欄位"]
