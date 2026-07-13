"""app/repos/messages.py 的 CRUD 測試。"""

from app.repos import messages as messages_repo
from app.repos import sessions as sessions_repo


async def test_add_and_list_messages_ordered_oldest_first(db_session):
    session = await sessions_repo.create_session(db_session)

    first = await messages_repo.add_message(db_session, session.id, "user", "你好")
    second = await messages_repo.add_message(db_session, session.id, "ai", "您好，有什麼可以幫忙？")

    result = await messages_repo.list_messages(db_session, session.id)

    assert [m.id for m in result] == [first.id, second.id]
    assert result[0].role == "user"
    assert result[1].role == "ai"


async def test_list_messages_empty(db_session):
    session = await sessions_repo.create_session(db_session)
    assert await messages_repo.list_messages(db_session, session.id) == []
