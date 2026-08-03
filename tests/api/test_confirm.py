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


async def test_confirm_can_be_retried_after_generation_finished(client, db_engine):
    """產出結束（phase=done）後可以再按一次重新產出。

    原本 WHERE 只認 phase='confirming'，失敗時使用者唯一的出路是
    「還原某個版本 → phase 回到 confirming → 再按確認」，沒人猜得到這條路。
    """
    import uuid

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.repos import sessions as sessions_repo

    session = await _create_confirming_session(client)

    first = await client.post(f"/api/v1/sessions/{session['id']}/confirm")
    assert first.status_code == 200

    # 模擬 worker 跑完：不論每份文件成敗，job 結束後 phase 都會落到 done
    async with async_sessionmaker(db_engine, expire_on_commit=False)() as db:
        await sessions_repo.update_session(db, uuid.UUID(session["id"]), phase="done")
        await db.commit()

    second = await client.post(f"/api/v1/sessions/{session['id']}/confirm")
    assert second.status_code == 200
    assert second.json()["job_id"] != first.json()["job_id"]


async def test_confirm_still_rejects_while_generating(client):
    """產出中（phase=generating）仍要擋下重複觸發，避免同一個 session 疊兩個 job。"""
    session = await _create_confirming_session(client)
    assert (await client.post(f"/api/v1/sessions/{session['id']}/confirm")).status_code == 200
    assert (await client.post(f"/api/v1/sessions/{session['id']}/confirm")).status_code == 409
