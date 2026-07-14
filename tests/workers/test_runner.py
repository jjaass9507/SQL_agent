"""runner：job 狀態機（claim queued→running→done/failed）、依 kind 分派 handler、
job 失敗（含未知 extra kind、缺欄位 payload）寫入 job.error。"""

import respx

from app.llm.provider import LLMProvider
from app.repos import jobs as jobs_repo
from app.repos import outputs as outputs_repo
from app.repos import sessions as sessions_repo
from app.rules.spec_models import ColumnSpec, TableSpec
from app.workers import runner
from tests.workers.conftest import BASE_URL, chat_completion_response, make_provider


def _tables_json() -> list[dict]:
    tables = [
        TableSpec(
            table_name="users",
            description="使用者",
            columns=[ColumnSpec("id", "uuid", False, "主鍵", is_primary_key=True)],
        )
    ]
    return [t.model_dump() for t in tables]


async def test_poll_once_runs_generate_job_end_to_end(session_factory, monkeypatch):
    async with session_factory() as db:
        session = await sessions_repo.create_session(db)
        job = await jobs_repo.create_job(
            db, session.id, kind="generate", payload_json={"tables": _tables_json()}
        )
        await db.commit()
        session_id, job_id = session.id, job.id

    provider = make_provider()
    monkeypatch.setattr(LLMProvider, "from_settings", classmethod(lambda cls, *a, **kw: provider))

    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(return_value=chat_completion_response("內容"))
        processed = await runner.poll_once(session_factory)

    assert processed == 1
    async with session_factory() as db:
        job = await jobs_repo.get_job(db, job_id)
        outputs = await outputs_repo.list_outputs(db, session_id)

    assert job.status == "done"
    assert job.started_at is not None
    assert job.finished_at is not None
    assert job.error is None
    assert len(outputs) == 4


async def test_poll_once_marks_generate_job_failed_when_payload_missing_tables(session_factory):
    async with session_factory() as db:
        session = await sessions_repo.create_session(db)
        job = await jobs_repo.create_job(db, session.id, kind="generate", payload_json={})
        await db.commit()
        job_id = job.id

    processed = await runner.poll_once(session_factory)
    assert processed == 1

    async with session_factory() as db:
        job = await jobs_repo.get_job(db, job_id)

    assert job.status == "failed"
    assert job.error is not None
    assert "tables" in job.error


async def test_poll_once_marks_extra_job_failed_for_unsupported_kind(session_factory):
    async with session_factory() as db:
        session = await sessions_repo.create_session(db)
        job = await jobs_repo.create_job(
            db,
            session.id,
            kind="extra",
            payload_json={"kind": "bogus", "tables": _tables_json()},
        )
        await db.commit()
        job_id = job.id

    processed = await runner.poll_once(session_factory)
    assert processed == 1

    async with session_factory() as db:
        job = await jobs_repo.get_job(db, job_id)

    assert job.status == "failed"
    assert job.error is not None


async def test_poll_once_runs_template_extra_job_with_zero_http_calls(session_factory):
    async with session_factory() as db:
        session = await sessions_repo.create_session(db)
        job = await jobs_repo.create_job(
            db,
            session.id,
            kind="extra",
            payload_json={"kind": "dbml", "tables": _tables_json()},
        )
        await db.commit()
        session_id, job_id = session.id, job.id

    with respx.mock(base_url=BASE_URL, assert_all_called=False) as mock:
        route = mock.post("/chat/completions").mock(
            return_value=chat_completion_response("不該被呼叫")
        )
        processed = await runner.poll_once(session_factory)

    assert processed == 1
    assert route.call_count == 0

    async with session_factory() as db:
        job = await jobs_repo.get_job(db, job_id)
        outputs = {o.filename: o.content for o in await outputs_repo.list_outputs(db, session_id)}

    assert job.status == "done"
    assert "Table users" in outputs["schema.dbml"]


async def test_poll_once_runs_review_job_end_to_end(session_factory, monkeypatch):
    async with session_factory() as db:
        session = await sessions_repo.create_session(
            db, mode="review", context_tables_json=_tables_json()
        )
        job = await jobs_repo.create_job(db, session.id, kind="review")
        await db.commit()
        session_id, job_id = session.id, job.id

    provider = make_provider()
    monkeypatch.setattr(LLMProvider, "from_settings", classmethod(lambda cls, *a, **kw: provider))

    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response("審查報告內容\n\n**整體評分：8/10**")
        )
        processed = await runner.poll_once(session_factory)

    assert processed == 1
    async with session_factory() as db:
        job = await jobs_repo.get_job(db, job_id)
        session = await sessions_repo.get_session(db, session_id)
        outputs = {o.filename for o in await outputs_repo.list_outputs(db, session_id)}

    assert job.status == "done"
    assert session.phase == "review_done"
    assert {"05_review_report.md", "06_review_fix.sql"} <= outputs
