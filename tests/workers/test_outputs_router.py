"""outputs router：/outputs 列表、/outputs/zip 下載、/extras/{kind}/generate 建 job、
/events SSE 進度推送順序。走真實 FastAPI app（httpx ASGI transport），覆蓋 get_db。
"""

import asyncio
import io
import json
import uuid
import zipfile

from app.api.routers import outputs as outputs_router
from app.repos import jobs as jobs_repo
from app.repos import outputs as outputs_repo
from app.repos import sessions as sessions_repo
from app.repos import versions as versions_repo
from app.rules.spec_models import ColumnSpec, TableSpec


def _sample_table_dict() -> dict:
    table = TableSpec(
        table_name="users",
        description="使用者",
        columns=[ColumnSpec("id", "uuid", False, "主鍵", is_primary_key=True)],
    )
    return table.model_dump()


async def test_list_outputs_returns_all_files(session_factory, client):
    async with session_factory() as db:
        session = await sessions_repo.create_session(db)
        await outputs_repo.upsert_output(db, session.id, "01_specification.md", "# 規格書")
        await outputs_repo.upsert_output(db, session.id, "03_ddl.sql", "CREATE TABLE users();")
        await db.commit()
        session_id = session.id

    resp = await client.get(f"/api/v1/sessions/{session_id}/outputs")
    assert resp.status_code == 200
    body = {item["filename"]: item["content"] for item in resp.json()}
    assert body["01_specification.md"] == "# 規格書"
    assert body["03_ddl.sql"] == "CREATE TABLE users();"


async def test_list_outputs_404_when_session_missing(client):
    resp = await client.get(f"/api/v1/sessions/{uuid.uuid4()}/outputs")
    assert resp.status_code == 404


async def test_download_zip_contains_all_outputs(session_factory, client):
    async with session_factory() as db:
        session = await sessions_repo.create_session(db)
        await outputs_repo.upsert_output(db, session.id, "01_specification.md", "# 規格書")
        await outputs_repo.upsert_output(db, session.id, "03_ddl.sql", "CREATE TABLE users();")
        await db.commit()
        session_id = session.id

    resp = await client.get(f"/api/v1/sessions/{session_id}/outputs/zip")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = set(zf.namelist())
        assert names == {"01_specification.md", "03_ddl.sql"}
        assert zf.read("01_specification.md").decode() == "# 規格書"


async def test_generate_extra_creates_queued_job(session_factory, client):
    async with session_factory() as db:
        session = await sessions_repo.create_session(db)
        await versions_repo.create_version(db, session.id, tables_json=[_sample_table_dict()])
        await db.commit()
        session_id = session.id

    resp = await client.post(f"/api/v1/sessions/{session_id}/extras/dbml/generate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"

    async with session_factory() as db:
        job = await jobs_repo.get_job(db, uuid.UUID(body["job_id"]))
    assert job is not None
    assert job.kind == "extra"
    assert job.payload_json["kind"] == "dbml"
    assert job.payload_json["tables"] == [_sample_table_dict()]


async def test_generate_extra_incremental_includes_context_tables(session_factory, client):
    context_table = _sample_table_dict()
    async with session_factory() as db:
        session = await sessions_repo.create_session(
            db, context_tables_json=[context_table]
        )
        await versions_repo.create_version(db, session.id, tables_json=[_sample_table_dict()])
        await db.commit()
        session_id = session.id

    resp = await client.post(f"/api/v1/sessions/{session_id}/extras/incremental/generate")
    assert resp.status_code == 200

    async with session_factory() as db:
        job = await jobs_repo.get_job(db, uuid.UUID(resp.json()["job_id"]))
    assert job.payload_json["context_tables"] == [context_table]


async def test_generate_extra_unsupported_kind_returns_400(session_factory, client):
    async with session_factory() as db:
        session = await sessions_repo.create_session(db)
        await versions_repo.create_version(db, session.id, tables_json=[_sample_table_dict()])
        await db.commit()
        session_id = session.id

    resp = await client.post(f"/api/v1/sessions/{session_id}/extras/bogus/generate")
    assert resp.status_code == 400


async def test_generate_extra_without_confirmed_tables_returns_400(session_factory, client):
    async with session_factory() as db:
        session = await sessions_repo.create_session(db)
        await db.commit()
        session_id = session.id

    resp = await client.post(f"/api/v1/sessions/{session_id}/extras/dbml/generate")
    assert resp.status_code == 400


# -- SSE 進度事件順序 ---------------------------------------------------------


async def _advance_job_through_states(session_factory, job_id) -> None:
    """背景模擬 job 狀態機推進：queued（已是初始值）→ running → progress 更新 → done。"""
    await asyncio.sleep(0.02)
    async with session_factory() as db:
        await jobs_repo.start_job(db, job_id)
        await db.commit()
    await asyncio.sleep(0.02)
    async with session_factory() as db:
        await jobs_repo.update_job_progress(db, job_id, {"01_specification.md": "loading"})
        await db.commit()
    await asyncio.sleep(0.02)
    async with session_factory() as db:
        await jobs_repo.update_job_progress(db, job_id, {"01_specification.md": "done"})
        await db.commit()
    await asyncio.sleep(0.02)
    async with session_factory() as db:
        await jobs_repo.finish_job(db, job_id, status="done")
        await db.commit()


async def test_sse_stream_emits_generation_status_events_in_order(session_factory):
    async with session_factory() as db:
        session = await sessions_repo.create_session(db)
        job = await jobs_repo.create_job(db, session.id, kind="generate", payload_json={})
        await db.commit()
        session_id, job_id = session.id, job.id

    advance_task = asyncio.create_task(_advance_job_through_states(session_factory, job_id))
    try:
        events: list[dict] = []
        async for chunk in outputs_router._job_progress_stream(
            session_id, job_id, session_factory, poll_interval=0.005
        ):
            assert chunk.startswith("event: generation_status\ndata: ")
            payload = json.loads(chunk.split("data: ", 1)[1].strip())
            events.append(payload)
    finally:
        await advance_task

    assert [e["job_id"] for e in events] == [str(job_id)] * len(events)
    statuses = [e["status"] for e in events]
    assert statuses[0] == "queued"
    assert statuses[-1] == "done"
    # 狀態單調遞增（queued → running → done/failed），不可逆退。
    rank = {"queued": 0, "running": 1, "done": 2, "failed": 2}
    ranks = [rank[s] for s in statuses]
    assert ranks == sorted(ranks)


async def test_sse_stream_ends_immediately_for_already_finished_job(session_factory):
    async with session_factory() as db:
        session = await sessions_repo.create_session(db)
        job = await jobs_repo.create_job(db, session.id, kind="generate", payload_json={})
        await jobs_repo.finish_job(db, job.id, status="done")
        await db.commit()
        session_id, job_id = session.id, job.id

    events = [
        chunk
        async for chunk in outputs_router._job_progress_stream(
            session_id, job_id, session_factory, poll_interval=0.005
        )
    ]
    assert len(events) == 1
    payload = json.loads(events[0].split("data: ", 1)[1].strip())
    assert payload["status"] == "done"


async def test_events_endpoint_streams_over_http(session_factory, client, monkeypatch):
    # `stream_generation_events` 內部用 `get_session_factory()`（全域單例，讀正式
    # DATABASE_URL）而非 `get_db` 依賴注入 —— SSE 長連線不能占用單一個 request-scoped
    # AsyncSession。測試改用 monkeypatch 把它指向本測試的 in-memory session_factory。
    monkeypatch.setattr(outputs_router, "get_session_factory", lambda: session_factory)

    async with session_factory() as db:
        session = await sessions_repo.create_session(db)
        job = await jobs_repo.create_job(db, session.id, kind="generate", payload_json={})
        await jobs_repo.finish_job(db, job.id, status="done")
        await db.commit()
        session_id = session.id

    resp = await client.get(f"/api/v1/sessions/{session_id}/events")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert "event: generation_status" in resp.text
    assert '"status": "done"' in resp.text
