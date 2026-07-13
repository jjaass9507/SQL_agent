"""app/repos/jobs.py 的測試：queued → running → done/failed 狀態轉移。"""

import uuid

from app.repos import jobs as jobs_repo
from app.repos import sessions as sessions_repo


async def test_create_job_defaults_to_queued(db_session):
    session = await sessions_repo.create_session(db_session)

    job = await jobs_repo.create_job(
        db_session, session.id, "generate", payload_json={"foo": "bar"}
    )

    assert job.status == "queued"
    assert job.started_at is None
    assert job.finished_at is None
    assert job.payload_json == {"foo": "bar"}


async def test_start_job_sets_running_and_started_at(db_session):
    session = await sessions_repo.create_session(db_session)
    job = await jobs_repo.create_job(db_session, session.id, "generate")

    started = await jobs_repo.start_job(db_session, job.id)

    assert started is not None
    assert started.status == "running"
    assert started.started_at is not None


async def test_finish_job_done(db_session):
    session = await sessions_repo.create_session(db_session)
    job = await jobs_repo.create_job(db_session, session.id, "review")
    await jobs_repo.start_job(db_session, job.id)

    finished = await jobs_repo.finish_job(db_session, job.id, status="done")

    assert finished is not None
    assert finished.status == "done"
    assert finished.finished_at is not None
    assert finished.error is None


async def test_finish_job_failed_records_error(db_session):
    session = await sessions_repo.create_session(db_session)
    job = await jobs_repo.create_job(db_session, session.id, "extra")

    finished = await jobs_repo.finish_job(db_session, job.id, status="failed", error="LLM timeout")

    assert finished is not None
    assert finished.status == "failed"
    assert finished.error == "LLM timeout"


async def test_update_job_progress(db_session):
    session = await sessions_repo.create_session(db_session)
    job = await jobs_repo.create_job(db_session, session.id, "generate")

    updated = await jobs_repo.update_job_progress(db_session, job.id, {"spec.md": "done"})

    assert updated is not None
    assert updated.progress_json == {"spec.md": "done"}


async def test_list_jobs(db_session):
    session = await sessions_repo.create_session(db_session)
    await jobs_repo.create_job(db_session, session.id, "generate")
    await jobs_repo.create_job(db_session, session.id, "review")

    result = await jobs_repo.list_jobs(db_session, session.id)

    assert len(result) == 2


async def test_job_not_found_returns_none(db_session):
    missing_id = uuid.uuid4()
    assert await jobs_repo.get_job(db_session, missing_id) is None
    assert await jobs_repo.start_job(db_session, missing_id) is None
    assert await jobs_repo.finish_job(db_session, missing_id, status="done") is None
