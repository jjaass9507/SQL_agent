"""llm_factory：業務呼叫點的 provider 必須套用 app_settings 留存的能力探測結果。"""

from app.repos import settings as settings_repo
from app.services import llm_factory


async def test_provider_uses_persisted_profile(db_session):
    """diagnose 存過的 profile（如全 False）要反映在 provider 上，降級才會生效。"""
    await settings_repo.set_setting(
        db_session,
        llm_factory.CAPABILITY_SETTING_KEY,
        {
            "multi_turn": False,
            "system_role": False,
            "native_tools": False,
            "json_schema": False,
            "streaming": False,
        },
    )
    provider = await llm_factory.provider_from_db(db_session)
    assert provider.profile.multi_turn is False
    assert provider.profile.streaming is False


async def test_provider_defaults_to_all_true_when_never_probed(db_session):
    provider = await llm_factory.provider_from_db(db_session)
    assert provider.profile.multi_turn is True
    assert provider.profile.streaming is True


async def test_force_profile_overrides_persisted(db_session, monkeypatch):
    """LLM_FORCE_PROFILE 優先於探測留存結果；未列出的欄位視為 true。"""
    from app.config import get_settings

    await settings_repo.set_setting(
        db_session, llm_factory.CAPABILITY_SETTING_KEY, {"multi_turn": True, "streaming": True}
    )
    monkeypatch.setenv("LLM_FORCE_PROFILE", '{"multi_turn": false, "streaming": false}')
    get_settings.cache_clear()
    try:
        provider = await llm_factory.provider_from_db(db_session)
    finally:
        get_settings.cache_clear()
    assert provider.profile.multi_turn is False
    assert provider.profile.streaming is False
    assert provider.profile.system_role is True  # 未列出 → 預設 true
