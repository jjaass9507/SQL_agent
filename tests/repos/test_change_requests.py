"""app/repos/change_requests.py 的測試：HITL 審批狀態轉移。"""

import uuid

from app.repos import change_requests as cr_repo


async def test_create_change_request_defaults_to_pending(db_session):
    record = await cr_repo.create_change_request(
        db_session, db_name="biz_db", ddl="ALTER TABLE t ADD COLUMN x INT"
    )

    assert record.status == "pending"
    assert record.decided_at is None


async def test_approve_sets_decided_at(db_session):
    record = await cr_repo.create_change_request(db_session, db_name="biz_db", ddl="...")

    approved = await cr_repo.decide_change_request(db_session, record.id, status="approved")

    assert approved is not None
    assert approved.status == "approved"
    assert approved.decided_at is not None


async def test_reject_sets_decided_at(db_session):
    record = await cr_repo.create_change_request(db_session, db_name="biz_db", ddl="...")

    rejected = await cr_repo.decide_change_request(db_session, record.id, status="rejected")

    assert rejected.status == "rejected"
    assert rejected.decided_at is not None


async def test_mark_executed_does_not_overwrite_decided_at(db_session):
    record = await cr_repo.create_change_request(db_session, db_name="biz_db", ddl="...")
    approved = await cr_repo.decide_change_request(db_session, record.id, status="approved")
    decided_at = approved.decided_at

    executed = await cr_repo.decide_change_request(db_session, record.id, status="executed")

    assert executed.status == "executed"
    assert executed.decided_at == decided_at


async def test_mark_failed_records_error(db_session):
    record = await cr_repo.create_change_request(db_session, db_name="biz_db", ddl="...")

    failed = await cr_repo.decide_change_request(
        db_session, record.id, status="failed", error="dry-run 失敗：語法錯誤"
    )

    assert failed.status == "failed"
    assert failed.error == "dry-run 失敗：語法錯誤"


async def test_list_change_requests_filters_by_status(db_session):
    await cr_repo.create_change_request(db_session, db_name="biz_db", ddl="a")
    r2 = await cr_repo.create_change_request(db_session, db_name="biz_db", ddl="b")
    await cr_repo.decide_change_request(db_session, r2.id, status="approved")

    pending = await cr_repo.list_change_requests(db_session, status="pending")
    approved = await cr_repo.list_change_requests(db_session, status="approved")

    assert len(pending) == 1
    assert len(approved) == 1
    assert approved[0].id == r2.id


async def test_decide_change_request_not_found(db_session):
    assert await cr_repo.decide_change_request(db_session, uuid.uuid4(), status="approved") is None
