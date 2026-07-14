"""generation_service：四文件並行產出、單檔失敗不影響其他、progress_json 逐步更新。"""

import json
from unittest.mock import AsyncMock, patch

import httpx
import respx

from app.repos import jobs as jobs_repo
from app.repos import outputs as outputs_repo
from app.repos import sessions as sessions_repo
from app.rules.spec_models import ColumnSpec, TableSpec
from app.services import generation_service
from tests.workers.conftest import (
    BASE_URL,
    chat_completion_response,
    dispatch_by_marker,
    make_provider,
)

_MARKERS = {
    "PostgreSQL DDL 腳本": "CREATE TABLE users (id uuid PRIMARY KEY);",
    "關聯設計決策": "使用者資料表獨立管理帳號資訊。",
    "效能與安全規劃書": "## 索引策略\n建議為 email 建索引。",
}


def _sample_tables() -> list[TableSpec]:
    return [
        TableSpec(
            table_name="users",
            description="使用者",
            columns=[
                ColumnSpec("id", "uuid", False, "主鍵", is_primary_key=True),
                ColumnSpec("email", "varchar", False, "電子郵件", length=255, is_unique=True),
            ],
        ),
    ]


async def _create_session_and_job(session_factory, **job_kwargs):
    async with session_factory() as db:
        session = await sessions_repo.create_session(db)
        job = await jobs_repo.create_job(db, session.id, kind="generate", **job_kwargs)
        await db.commit()
        return session.id, job.id


async def test_four_documents_generated_written_to_outputs_and_progress_done(session_factory):
    session_id, job_id = await _create_session_and_job(session_factory)
    tables = _sample_tables()

    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(side_effect=dispatch_by_marker(_MARKERS))
        provider = make_provider()
        results = await generation_service.generate_documents(
            job_id, session_id, tables, provider=provider, session_factory=session_factory
        )

    assert set(results) == set(generation_service.FILENAMES)
    assert all(v is not None for v in results.values())

    async with session_factory() as db:
        outputs = {o.filename: o.content for o in await outputs_repo.list_outputs(db, session_id)}
        job = await jobs_repo.get_job(db, job_id)

    assert "users" in outputs["01_specification.md"]
    assert "email" in outputs["01_specification.md"]
    assert "CREATE TABLE" in outputs["03_ddl.sql"]
    assert "```mermaid" in outputs["02_er_diagram.md"]
    assert "使用者資料表獨立管理帳號資訊" in outputs["02_er_diagram.md"]
    assert "索引策略" in outputs["04_security_plan.md"]
    assert job.progress_json == dict.fromkeys(generation_service.FILENAMES, "done")


async def test_single_document_failure_does_not_affect_others(session_factory):
    session_id, job_id = await _create_session_and_job(session_factory)
    tables = _sample_tables()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        system_content = next(m["content"] for m in body["messages"] if m["role"] == "system")
        if "PostgreSQL DDL 腳本" in system_content:
            return httpx.Response(500, json={"error": {"message": "boom"}})
        for marker, content in _MARKERS.items():
            if marker in system_content:
                return chat_completion_response(content)
        return chat_completion_response("")

    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(side_effect=handler)
        provider = make_provider()
        with patch("app.llm.provider.asyncio.sleep", new=AsyncMock()):
            results = await generation_service.generate_documents(
                job_id, session_id, tables, provider=provider, session_factory=session_factory
            )

    assert results["03_ddl.sql"] is None
    assert results["01_specification.md"] is not None
    assert results["02_er_diagram.md"] is not None
    assert results["04_security_plan.md"] is not None

    async with session_factory() as db:
        job = await jobs_repo.get_job(db, job_id)
        output_names = {o.filename for o in await outputs_repo.list_outputs(db, session_id)}

    assert job.progress_json["03_ddl.sql"] == "failed"
    assert job.progress_json["01_specification.md"] == "done"
    assert job.progress_json["02_er_diagram.md"] == "done"
    assert job.progress_json["04_security_plan.md"] == "done"
    assert "03_ddl.sql" not in output_names
    assert "01_specification.md" in output_names


async def test_progress_json_transitions_waiting_loading_done_per_file(
    session_factory, monkeypatch
):
    session_id, job_id = await _create_session_and_job(session_factory)
    tables = _sample_tables()

    snapshots: list[dict] = []
    original_set_progress = generation_service._set_progress

    async def spy(session_factory_, job_id_, progress, lock_):
        snapshots.append(dict(progress))
        await original_set_progress(session_factory_, job_id_, progress, lock_)

    monkeypatch.setattr(generation_service, "_set_progress", spy)

    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/chat/completions").mock(side_effect=dispatch_by_marker(_MARKERS))
        provider = make_provider()
        await generation_service.generate_documents(
            job_id, session_id, tables, provider=provider, session_factory=session_factory
        )

    assert snapshots[0] == dict.fromkeys(generation_service.FILENAMES, "waiting")
    for filename in generation_service.FILENAMES:
        seq = [s[filename] for s in snapshots]
        changes = [seq[0]]
        for value in seq[1:]:
            if value != changes[-1]:
                changes.append(value)
        assert changes == ["waiting", "loading", "done"]
