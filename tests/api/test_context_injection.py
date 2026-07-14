"""既有 DB context 注入規則：提及既有表名即注入，且此後 sticky。"""

import json

import respx

from app.rules.spec_models import tables_from_json
from app.services import session_service
from tests.api.conftest import BASE_URL, interview_turn_payload, sample_table
from tests.llm.conftest import chat_completion_response


async def _create_review_session(client, monkeypatch, table_name: str = "users") -> dict:
    tables_json = [sample_table(table_name)]

    async def _fake_schema_tree(db_url):
        return tables_from_json(tables_json), ""

    monkeypatch.setattr(session_service.dbops, "schema_tree", _fake_schema_tree)

    resp = await client.post(
        "/api/v1/sessions",
        json={"mode": "review", "db_url": "postgresql://u:p@h/db"},
    )
    return resp.json()


async def test_mentioning_existing_table_injects_context_and_stays_sticky(client, monkeypatch):
    session = await _create_review_session(client, monkeypatch, "users")

    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions")
        route.side_effect = [
            chat_completion_response(content=interview_turn_payload("好的，我會關聯 users 表")),
            chat_completion_response(content=interview_turn_payload("已新增地址欄位設計")),
        ]

        await client.post(
            f"/api/v1/sessions/{session['id']}/messages",
            json={"content": "我想要新增一個和 users 表關聯的訂單表"},
        )
        await client.post(
            f"/api/v1/sessions/{session['id']}/messages",
            json={"content": "請幫我加一個地址欄位"},
        )

    first_body = json.loads(route.calls[0].request.content)
    second_body = json.loads(route.calls[1].request.content)
    assert "--- 現有資料庫結構" in first_body["messages"][0]["content"]
    # sticky：第二輪即使沒提到既有表名，仍持續注入
    assert "--- 現有資料庫結構" in second_body["messages"][0]["content"]


async def test_unrelated_turn_does_not_inject_context(client, monkeypatch):
    session = await _create_review_session(client, monkeypatch, "users")

    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/chat/completions")
        route.mock(return_value=chat_completion_response(content=interview_turn_payload("好的")))

        await client.post(
            f"/api/v1/sessions/{session['id']}/messages",
            json={"content": "幫我設計一個跟訂單完全無關的日誌表"},
        )

    body = json.loads(route.calls[0].request.content)
    assert "--- 現有資料庫結構" not in body["messages"][0]["content"]
