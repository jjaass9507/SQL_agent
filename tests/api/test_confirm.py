"""POST /sessions/{id}/confirm：原子防重複轉換 phase + 建立 generate job。"""

import respx

from tests.api.conftest import BASE_URL, interview_turn_payload, sample_table
from tests.llm.conftest import chat_completion_response


async def _create_confirming_session(client) -> dict:
    session = (await client.post("/api/v1/sessions", json={})).json()
    tables = [sample_table("users")]
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(
                content=interview_turn_payload("設計完成", tables=tables, summary=["需求"])
            )
        )
        await client.post(
            f"/api/v1/sessions/{session['id']}/messages",
            json={"content": "我要一張使用者表"},
        )
    return session


async def test_confirm_creates_generate_job_and_transitions_phase(client):
    session = await _create_confirming_session(client)

    resp = await client.post(f"/api/v1/sessions/{session['id']}/confirm")

    assert resp.status_code == 200
    body = resp.json()
    assert body["phase"] == "generating"
    assert body["job_id"]

    detail = (await client.get(f"/api/v1/sessions/{session['id']}")).json()
    assert detail["phase"] == "generating"
    jobs = [j for j in detail["jobs"] if j["kind"] == "generate"]
    assert len(jobs) == 1
    assert jobs[0]["status"] == "queued"


async def test_confirm_twice_returns_409(client):
    session = await _create_confirming_session(client)
    first = await client.post(f"/api/v1/sessions/{session['id']}/confirm")
    assert first.status_code == 200

    second = await client.post(f"/api/v1/sessions/{session['id']}/confirm")

    assert second.status_code == 409


async def test_confirm_while_still_collecting_returns_409(client):
    session = (await client.post("/api/v1/sessions", json={})).json()

    resp = await client.post(f"/api/v1/sessions/{session['id']}/confirm")

    assert resp.status_code == 409


async def test_confirm_session_not_found_returns_404(client):
    resp = await client.post(
        "/api/v1/sessions/00000000-0000-0000-0000-000000000000/confirm"
    )

    assert resp.status_code == 404
