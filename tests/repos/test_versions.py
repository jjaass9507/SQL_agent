"""app/repos/versions.py 的測試：version_num 遞增、上限 10 版、超過刪最舊。"""

from app.repos import sessions as sessions_repo
from app.repos import versions as versions_repo


async def test_create_version_increments_version_num(db_session):
    session = await sessions_repo.create_session(db_session)

    v1 = await versions_repo.create_version(
        db_session, session.id, tables_json=[{"table_name": "a"}]
    )
    v2 = await versions_repo.create_version(
        db_session, session.id, tables_json=[{"table_name": "b"}]
    )

    assert v1.version_num == 1
    assert v2.version_num == 2


async def test_get_latest_version(db_session):
    session = await sessions_repo.create_session(db_session)
    await versions_repo.create_version(db_session, session.id, tables_json=[])
    v2 = await versions_repo.create_version(db_session, session.id, tables_json=[])

    latest = await versions_repo.get_latest_version(db_session, session.id)

    assert latest is not None
    assert latest.id == v2.id


async def test_get_version_by_num(db_session):
    session = await sessions_repo.create_session(db_session)
    v1 = await versions_repo.create_version(db_session, session.id, tables_json=[])

    fetched = await versions_repo.get_version(db_session, session.id, 1)

    assert fetched is not None
    assert fetched.id == v1.id
    assert await versions_repo.get_version(db_session, session.id, 999) is None


async def test_version_cap_deletes_oldest(db_session):
    session = await sessions_repo.create_session(db_session)

    for i in range(12):
        await versions_repo.create_version(db_session, session.id, tables_json=[{"n": i}])

    remaining = await versions_repo.list_versions(db_session, session.id)

    assert len(remaining) == versions_repo.MAX_VERSIONS_PER_SESSION
    version_nums = sorted(v.version_num for v in remaining)
    # 前兩版（1、2）應已被刪除，只留下最新的 10 版（3～12）
    assert version_nums == list(range(3, 13))


async def test_list_versions_ordered_newest_first(db_session):
    session = await sessions_repo.create_session(db_session)
    await versions_repo.create_version(db_session, session.id, tables_json=[])
    v2 = await versions_repo.create_version(db_session, session.id, tables_json=[])

    result = await versions_repo.list_versions(db_session, session.id)

    assert result[0].id == v2.id
