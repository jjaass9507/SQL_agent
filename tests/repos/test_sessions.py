"""app/repos/sessions.py 的 CRUD 測試。"""

import uuid

from app.repos import sessions as sessions_repo


async def test_create_and_get_session(db_session):
    record = await sessions_repo.create_session(db_session, title="我的設計", mode="design")
    fetched = await sessions_repo.get_session(db_session, record.id)

    assert fetched is not None
    assert fetched.title == "我的設計"
    assert fetched.mode == "design"
    assert fetched.phase == "collecting"  # 預設值
    assert fetched.context_text == ""


async def test_get_session_not_found(db_session):
    assert await sessions_repo.get_session(db_session, uuid.uuid4()) is None


async def test_list_sessions_ordered_newest_first(db_session):
    first = await sessions_repo.create_session(db_session, title="first")
    second = await sessions_repo.create_session(db_session, title="second")

    result = await sessions_repo.list_sessions(db_session)

    assert [r.id for r in result] == [second.id, first.id]


async def test_list_sessions_filters_by_user(db_session):
    user_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    await sessions_repo.create_session(db_session, title="mine", user_id=user_id)
    await sessions_repo.create_session(db_session, title="theirs", user_id=other_user_id)

    result = await sessions_repo.list_sessions(db_session, user_id=user_id)

    assert len(result) == 1
    assert result[0].title == "mine"


async def test_update_session(db_session):
    record = await sessions_repo.create_session(db_session, title="原本標題")

    updated = await sessions_repo.update_session(
        db_session, record.id, title="新標題", phase="confirming"
    )

    assert updated is not None
    assert updated.title == "新標題"
    assert updated.phase == "confirming"


async def test_update_session_not_found(db_session):
    assert await sessions_repo.update_session(db_session, uuid.uuid4(), title="x") is None


async def test_delete_session(db_session):
    record = await sessions_repo.create_session(db_session, title="待刪除")

    deleted = await sessions_repo.delete_session(db_session, record.id)
    fetched = await sessions_repo.get_session(db_session, record.id)

    assert deleted is True
    assert fetched is None


async def test_delete_session_not_found(db_session):
    assert await sessions_repo.delete_session(db_session, uuid.uuid4()) is False
