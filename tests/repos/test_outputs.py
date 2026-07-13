"""app/repos/outputs.py 的 CRUD 測試（(session_id, filename) 唯一，upsert 語意）。"""

from app.repos import outputs as outputs_repo
from app.repos import sessions as sessions_repo


async def test_upsert_output_creates(db_session):
    session = await sessions_repo.create_session(db_session)

    record = await outputs_repo.upsert_output(db_session, session.id, "spec.md", "# 規格書")

    assert record.filename == "spec.md"
    assert record.content == "# 規格書"


async def test_upsert_output_updates_existing(db_session):
    session = await sessions_repo.create_session(db_session)
    await outputs_repo.upsert_output(db_session, session.id, "spec.md", "第一版內容")

    updated = await outputs_repo.upsert_output(db_session, session.id, "spec.md", "第二版內容")
    all_outputs = await outputs_repo.list_outputs(db_session, session.id)

    assert updated.content == "第二版內容"
    assert len(all_outputs) == 1  # 沒有因為 upsert 而重複建立


async def test_get_output_not_found(db_session):
    session = await sessions_repo.create_session(db_session)
    assert await outputs_repo.get_output(db_session, session.id, "missing.md") is None


async def test_list_outputs(db_session):
    session = await sessions_repo.create_session(db_session)
    await outputs_repo.upsert_output(db_session, session.id, "b.sql", "b")
    await outputs_repo.upsert_output(db_session, session.id, "a.md", "a")

    result = await outputs_repo.list_outputs(db_session, session.id)

    assert [o.filename for o in result] == ["a.md", "b.sql"]


async def test_delete_output(db_session):
    session = await sessions_repo.create_session(db_session)
    await outputs_repo.upsert_output(db_session, session.id, "spec.md", "内容")

    deleted = await outputs_repo.delete_output(db_session, session.id, "spec.md")

    assert deleted is True
    assert await outputs_repo.get_output(db_session, session.id, "spec.md") is None


async def test_delete_output_not_found(db_session):
    session = await sessions_repo.create_session(db_session)
    assert await outputs_repo.delete_output(db_session, session.id, "missing.md") is False
