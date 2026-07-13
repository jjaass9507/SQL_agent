"""app/repos/activity.py 的測試（結構化 audit log）。"""

from app.repos import activity as activity_repo


async def test_log_and_list_activity(db_session):
    await activity_repo.log_activity(db_session, "session.create", {"session_id": "abc"})
    await activity_repo.log_activity(db_session, "session.confirm", {"session_id": "abc"})

    result = await activity_repo.list_activity(db_session)

    assert len(result) == 2
    assert result[0].event == "session.confirm"  # 新到舊排序
    assert result[1].event == "session.create"


async def test_list_activity_respects_limit(db_session):
    for i in range(5):
        await activity_repo.log_activity(db_session, f"event.{i}")

    result = await activity_repo.list_activity(db_session, limit=2)

    assert len(result) == 2
