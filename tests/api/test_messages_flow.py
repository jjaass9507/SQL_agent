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
