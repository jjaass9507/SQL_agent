"""app/repos/settings.py 的 CRUD 測試（app_settings 鍵值表）。"""

from app.repos import settings as settings_repo


async def test_set_and_get_setting(db_session):
    await settings_repo.set_setting(db_session, "llm_capability_profile", {"multi_turn": True})

    fetched = await settings_repo.get_setting(db_session, "llm_capability_profile")

    assert fetched is not None
    assert fetched.value_json == {"multi_turn": True}


async def test_set_setting_upserts(db_session):
    await settings_repo.set_setting(db_session, "k", {"v": 1})
    updated = await settings_repo.set_setting(db_session, "k", {"v": 2})

    assert updated.value_json == {"v": 2}
    assert len(await settings_repo.list_settings(db_session)) == 1


async def test_get_setting_not_found(db_session):
    assert await settings_repo.get_setting(db_session, "missing") is None


async def test_delete_setting(db_session):
    await settings_repo.set_setting(db_session, "k", "v")

    deleted = await settings_repo.delete_setting(db_session, "k")

    assert deleted is True
    assert await settings_repo.get_setting(db_session, "k") is None


async def test_delete_setting_not_found(db_session):
    assert await settings_repo.delete_setting(db_session, "missing") is False


async def test_list_settings(db_session):
    await settings_repo.set_setting(db_session, "b", 2)
    await settings_repo.set_setting(db_session, "a", 1)

    result = await settings_repo.list_settings(db_session)

    assert [s.key for s in result] == ["a", "b"]
