"""review_service：context_tables → Reviewer（LLM）全流程 + 規則式紅旗/修復 SQL + phase 轉移。"""

import uuid

import pytest
import respx

from app.repos import jobs as jobs_repo
from app.repos import outputs as outputs_repo
from app.repos import sessions as sessions_repo
from app.rules.spec_models import ColumnSpec, TableSpec
from app.services import review_service
from tests.workers.conftest import BASE_URL, chat_completion_response, make_provider


def _existing_tables() -> list[TableSpec]:
    return [
        TableSpec(
            table_name="users",
            description="使用者",
            columns=[
                ColumnSpec("id", "uuid", False, "主鍵", is_primary_key=True),
                ColumnSpec("password", "varchar", False, "密碼", length=100),
            ],
        )
    ]


async def test_run_review_writes_report_and_fix_sql_and_updates_phase(session_factory):
    tables = _existing_tables()
    async with session_factory() as db:
        session = await sessions_repo.create_session(
            db,
            mode="review",
            context_tables_json=[t.model_dump() for t in tables],
        )
        job = await jobs_repo.create_job(db, session.id, kind="review")
        await db.commit()
        session_id, job_id = session.id, job.id

    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(
            return_value=chat_completion_response(
                "## 1. 設計一致性\n- **users**：命名一致。\n\n**整體評分：7/10**"
            )
        )
        provider = make_provider()
        async with session_factory() as db:
            job = await jobs_repo.get_job(db, job_id)
            await review_service.run_review(db, job, provider=provider)
            await db.commit()

    async with session_factory() as db:
        outputs = {o.filename: o.content for o in await outputs_repo.list_outputs(db, session_id)}
        session = await sessions_repo.get_session(db, session_id)

    assert "整體評分：7/10" in outputs["05_review_report.md"]
    # password 欄位命中 schema_advisor 的敏感欄位紅旗，remediation 產出對應的 TODO 註解
    assert "敏感資料" in outputs["06_review_fix.sql"]
    assert session.phase == "review_done"


async def test_run_review_raises_when_session_has_no_context_tables(session_factory):
    async with session_factory() as db:
        session = await sessions_repo.create_session(db, mode="review", context_tables_json=[])
        job = await jobs_repo.create_job(db, session.id, kind="review")
        await db.commit()
        job_id = job.id

    async with session_factory() as db:
        job = await jobs_repo.get_job(db, job_id)
        with pytest.raises(ValueError):
            await review_service.run_review(db, job)


async def test_run_review_raises_when_session_missing(session_factory):
    async with session_factory() as db:
        job = await jobs_repo.create_job(db, uuid.uuid4(), kind="review")
        await db.commit()
        job_id = job.id

    async with session_factory() as db:
        job = await jobs_repo.get_job(db, job_id)
        with pytest.raises(ValueError):
            await review_service.run_review(db, job)
