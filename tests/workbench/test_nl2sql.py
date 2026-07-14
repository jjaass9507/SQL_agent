"""POST /sessions/{id}/nl2sql：mock LLM 回合法/非法 SQL 兩情境。"""

import uuid

import respx

from app.config import get_settings
from tests.llm.conftest import BASE_URL, chat_completion_response


def _set_llm_env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", BASE_URL)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    get_settings.cache_clear()


async def test_nl2sql_session_not_found(client):
    resp = await client.post(
        f"/api/v1/sessions/{uuid.uuid4()}/nl2sql", json={"question": "有多少使用者？"}
    )
    assert resp.status_code == 404


async def test_nl2sql_no_db_url(client, make_session):
    record = await make_session()
    resp = await client.post(
        f"/api/v1/sessions/{record.id}/nl2sql", json={"question": "有多少使用者？"}
    )
    assert resp.status_code == 400


async def test_nl2sql_valid_sql_returns_draft(client, make_session, monkeypatch):
    _set_llm_env(monkeypatch)
    record = await make_session(db_url="sqlite://")

    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(
                content='{"sql": "SELECT COUNT(*) FROM users", "explanation": "計算使用者數"}'
            )
        )
        resp = await client.post(
            f"/api/v1/sessions/{record.id}/nl2sql", json={"question": "有多少使用者？"}
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["sql"] == "SELECT COUNT(*) FROM users"
    assert body["explanation"] == "計算使用者數"


async def test_nl2sql_rejects_non_read_only_sql_from_llm(client, make_session, monkeypatch):
    _set_llm_env(monkeypatch)
    record = await make_session(db_url="sqlite://")

    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(
                content='{"sql": "DELETE FROM users", "explanation": "刪除所有使用者"}'
            )
        )
        resp = await client.post(
            f"/api/v1/sessions/{record.id}/nl2sql", json={"question": "刪掉所有使用者"}
        )

    assert resp.status_code == 400
